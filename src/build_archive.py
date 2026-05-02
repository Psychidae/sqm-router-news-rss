#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import news_to_slack


def load_archive_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config.get("categories"), list) or not config["categories"]:
        raise ValueError("archive config must contain a non-empty categories list")
    return config


def project_root_for_config(path: Path) -> Path:
    parent = path.resolve().parent
    if parent.name == "config":
        return parent.parent
    return parent


def resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def year_windows(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    windows: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(dt.date(cursor.year, 12, 31), end)
        windows.append((cursor, window_end))
        cursor = window_end + dt.timedelta(days=1)
    return windows


def default_start_date(years: int, today: dt.date) -> dt.date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def archive_id(url: str, title: str, published_at: str | None) -> str:
    seed = news_to_slack.normalize_url(url) or f"{title}\n{published_at or ''}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def item_matches(item: dict[str, Any], required: list[str], excluded: list[str]) -> bool:
    text = f"{item.get('title', '')}\n{item.get('summary', '')}".lower()
    if news_to_slack.has_excluded_term(text, excluded):
        return False
    return news_to_slack.matches_terms(text, required)


def seed_items_from_current_apis(project_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw_path in config.get("seed_api_files", []):
        path = resolve_path(project_root, str(raw_path))
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        category_id = category_id_for_seed_path(path.name)
        for raw in data.get("items", []):
            url = str(raw.get("url", ""))
            title = str(raw.get("title", ""))
            published_at = raw.get("published_at")
            items.append(
                {
                    "id": archive_id(url, title, published_at),
                    "category_id": category_id,
                    "category": data.get("title", category_id),
                    "title": title,
                    "url": url,
                    "summary": str(raw.get("summary", "")),
                    "published_at": published_at,
                    "source": str(raw.get("source", "")),
                    "source_url": str(raw.get("source_url", "")),
                    "info_type": str(raw.get("info_type", "")),
                    "discovery_source": "current_api_seed",
                }
            )
    return items


def category_id_for_seed_path(name: str) -> str:
    mapping = {
        "latest.json": "sqm-domestic",
        "router-domestic.json": "router-domestic",
        "router-global.json": "router-global",
        "network-security.json": "network-security",
        "network-performance.json": "network-performance",
    }
    return mapping.get(name, Path(name).stem)


def brave_search(
    query: str,
    category: dict[str, Any],
    start: dt.date,
    end: dt.date,
    api_key: str,
    user_agent: str,
    timeout: int,
    count: int,
) -> list[dict[str, Any]]:
    url = news_to_slack.build_url(
        "https://api.search.brave.com/res/v1/web/search",
        {
            "q": query,
            "count": count,
            "country": category.get("country", "us"),
            "search_lang": category.get("search_lang", "en"),
            "freshness": f"{start.isoformat()}to{end.isoformat()}",
            "spellcheck": 1,
        },
    )
    data = news_to_slack.fetch_json(
        url,
        user_agent=user_agent,
        timeout=timeout,
        extra_headers={"X-Subscription-Token": api_key},
    )
    raw_results = data.get("web", {}).get("results", [])
    items: list[dict[str, Any]] = []
    for raw in raw_results:
        title = news_to_slack.clean_text(raw.get("title"))
        result_url = str(raw.get("url", ""))
        snippets = raw.get("extra_snippets") or []
        summary = news_to_slack.clean_text(" ".join([raw.get("description", ""), *snippets]))
        published = news_to_slack.parse_datetime(raw.get("page_age") or raw.get("age"))
        published_at = news_to_slack.iso_datetime(published)
        source = raw.get("profile", {}).get("name") if isinstance(raw.get("profile"), dict) else ""
        items.append(
            {
                "id": archive_id(result_url, title, published_at),
                "category_id": category["id"],
                "category": category.get("title", category["id"]),
                "title": title or "(no title)",
                "url": result_url,
                "summary": summary,
                "published_at": published_at,
                "source": source or "Brave Search",
                "source_url": url,
                "info_type": category.get("title", category["id"]),
                "discovery_source": "brave_web_backfill",
                "search_window": f"{start.isoformat()}..{end.isoformat()}",
                "query": query,
            }
        )
    return items


def collect_backfill_items(
    config: dict[str, Any],
    start: dt.date,
    end: dt.date,
    api_key: str | None,
    user_agent: str,
    timeout: int,
    count: int,
    sleep_seconds: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not api_key:
        return [], ["BRAVE_SEARCH_API_KEY is not set; historical Brave backfill was skipped."]

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    excluded = list(config.get("exclude_any", []))
    windows = year_windows(start, end)
    for category in config["categories"]:
        required = list(category.get("required_any", []))
        for query in category.get("queries", []):
            for window_start, window_end in windows:
                try:
                    parsed = brave_search(
                        str(query),
                        category,
                        window_start,
                        window_end,
                        api_key,
                        user_agent,
                        timeout,
                        count,
                    )
                except Exception as exc:
                    errors.append(f"{category['id']} {window_start.year}: {exc}")
                    continue
                for item in parsed:
                    if item_matches(item, required, excluded):
                        items.append(item)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
    return items, errors


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = str(item.get("id", ""))
        if not item_id:
            continue
        if item_id not in by_id:
            by_id[item_id] = item
            continue
        existing = by_id[item_id]
        if not existing.get("published_at") and item.get("published_at"):
            by_id[item_id] = {**existing, **item}
    return sorted(
        by_id.values(),
        key=lambda item: item.get("published_at") or "",
        reverse=True,
    )


def category_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("category_id", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["category_id", "category", "published_at", "title", "url", "source", "summary", "discovery_source"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)


def item_rows(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        title = html.escape(str(item.get("title", "")))
        url = html.escape(str(item.get("url", "")))
        category = html.escape(str(item.get("category", "")))
        published = html.escape(str(item.get("published_at", "") or ""))
        source = html.escape(str(item.get("source", "")))
        summary = html.escape(news_to_slack.truncate(str(item.get("summary", "")), 220))
        rows.append(
            "<tr>"
            f"<td>{published}</td>"
            f"<td>{category}</td>"
            f"<td><a href=\"{url}\">{title}</a><br><small>{summary}</small></td>"
            f"<td>{source}</td>"
            "</tr>"
        )
    return "\n      ".join(rows)


def write_html(path: Path, title: str, description: str, items: list[dict[str, Any]], payload_links: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1160px; margin: 40px auto; padding: 0 20px; line-height: 1.65; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border-bottom: 1px solid #d8dee6; padding: 10px 8px; text-align: left; vertical-align: top; }}
    small {{ color: #52616f; }}
    a {{ color: #0f5bd8; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>{html.escape(description)}</p>
  <p>{payload_links}</p>
  <table>
    <thead><tr><th>日付</th><th>ジャンル</th><th>記事</th><th>ソース</th></tr></thead>
    <tbody>
      {item_rows(items)}
    </tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_outputs(output_dir: Path, config: dict[str, Any], items: list[dict[str, Any]], errors: list[str]) -> None:
    generated_at = news_to_slack.iso_datetime(dt.datetime.now(dt.timezone.utc))
    counts = category_counts(items)
    payload = {
        "title": config.get("title", "Router / Network News Archive"),
        "description": config.get("description", ""),
        "generated_at": generated_at,
        "count": len(items),
        "counts": counts,
        "errors": errors,
        "items": items,
    }
    write_json(output_dir / "archive.json", payload)
    write_csv(output_dir / "archive.csv", items)

    links = '<a href="archive.json">JSON</a> / <a href="archive.csv">CSV</a>'
    write_html(
        output_dir / "index.html",
        str(payload["title"]),
        f"{payload['description']} 現在の登録件数: {len(items)}件。",
        items,
        links,
    )

    for category in config["categories"]:
        category_id = category["id"]
        category_items = [item for item in items if item.get("category_id") == category_id]
        category_payload = {
            "title": category.get("title", category_id),
            "generated_at": generated_at,
            "count": len(category_items),
            "items": category_items,
        }
        write_json(output_dir / f"{category_id}.json", category_payload)
        write_html(
            output_dir / f"{category_id}.html",
            str(category.get("title", category_id)),
            f"{category.get('title', category_id)} の過去記事リストです。登録件数: {len(category_items)}件。",
            category_items,
            f'<a href="{category_id}.json">JSON</a>',
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build historical router/network news archive.")
    parser.add_argument("--config", default="config/archive.json", help="archive config path")
    parser.add_argument("--start-date", help="YYYY-MM-DD; defaults to config.start_date")
    parser.add_argument("--end-date", help="YYYY-MM-DD; defaults to today")
    parser.add_argument("--years", type=int, help="backfill years from end date; overrides config.start_date")
    parser.add_argument("--output-dir", help="output directory; defaults to config.output_dir")
    parser.add_argument("--api-key-env", default="BRAVE_SEARCH_API_KEY", help="Brave Search API key env var")
    parser.add_argument("--count", type=int, default=20, help="Brave results per query/window")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    parser.add_argument("--no-seed-current", action="store_true", help="do not seed archive from current API files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config_path = Path(args.config).expanduser().resolve()
    config = load_archive_config(config_path)
    project_root = project_root_for_config(config_path)
    news_to_slack.load_dotenv(resolve_path(project_root, ".env"))

    end = parse_date(args.end_date) if args.end_date else dt.datetime.now(dt.timezone.utc).date()
    if args.years is not None:
        start = default_start_date(args.years, end)
    elif args.start_date:
        start = parse_date(args.start_date)
    else:
        start = parse_date(str(config.get("start_date"))) if config.get("start_date") else default_start_date(int(config.get("default_years", 10)), end)

    output_dir = resolve_path(project_root, args.output_dir or str(config.get("output_dir", "docs/archive")))
    seed_items = [] if args.no_seed_current else seed_items_from_current_apis(project_root, config)
    backfill_items, errors = collect_backfill_items(
        config,
        start,
        end,
        os.environ.get(args.api_key_env),
        "router-network-news-archive/0.1",
        args.timeout,
        args.count,
        args.sleep_seconds,
    )
    items = dedupe_items([*seed_items, *backfill_items])
    write_outputs(output_dir, config, items, errors)

    print(f"archive: {len(items)} 件 -> {output_dir / 'index.html'}")
    if errors:
        for error in errors:
            print(f"warning: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
