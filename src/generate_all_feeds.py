#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

import news_to_slack


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest.get("feeds"), list) or not manifest["feeds"]:
        raise ValueError("manifest must contain a non-empty feeds list")
    return manifest


def project_root_for_manifest(path: Path) -> Path:
    parent = path.resolve().parent
    if parent.name == "config":
        return parent.parent
    return parent


def resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def docs_relative_path(project_root: Path, path: Path) -> str:
    docs_root = project_root / "docs"
    try:
        return path.resolve().relative_to(docs_root.resolve()).as_posix()
    except ValueError:
        return path.name


def set_output_paths(config: dict[str, Any], project_root: Path, rss_path: Path, json_path: Path) -> None:
    feed = config.setdefault("feed", {})
    feed.setdefault("feed_path", docs_relative_path(project_root, rss_path))
    feed.setdefault("api_path", docs_relative_path(project_root, json_path))


def generate_one(entry: dict[str, Any], project_root: Path) -> dict[str, Any]:
    config_path = resolve_path(project_root, str(entry["config"]))
    config = news_to_slack.load_config(config_path)
    config["_project_root"] = str(project_root)

    app = config.setdefault("app", {})
    env_file_value = app.get("env_file", ".env")
    news_to_slack.load_dotenv(news_to_slack.resolve_project_path(project_root, env_file_value))

    rss_path = resolve_path(project_root, str(entry["rss_output"]))
    json_path = resolve_path(project_root, str(entry["json_output"]))
    set_output_paths(config, project_root, rss_path, json_path)
    news_to_slack.apply_runtime_feed_defaults(config)

    items, errors, _state, _state_path = news_to_slack.collect_items(config, ignore_state=True)
    for error in errors:
        print(f"warning: {entry.get('id', config_path.name)}: {error}", file=sys.stderr)

    preserve_existing = bool(config.get("feed", {}).get("preserve_existing_on_error", True))
    existing_paths = [path for path in [rss_path, json_path] if path.exists()]
    if preserve_existing and errors and not items and existing_paths:
        print(f"{entry['id']}: 取得エラーがあり新規項目が0件のため既存公開ファイルを保持しました")
    else:
        existing_first_seen_dates = news_to_slack.load_existing_rss_first_seen_dates(rss_path)
        rss_item_dates = news_to_slack.resolve_rss_item_dates(
            items,
            config,
            existing_first_seen_dates,
            dt.datetime.now(dt.timezone.utc),
        )
        news_to_slack.write_rss_feed(rss_path, items, config, rss_item_dates)
        news_to_slack.write_json_api(json_path, items, config, errors)

    feed_config = config.get("feed", {})
    return {
        "id": entry.get("id", config_path.stem),
        "title": entry.get("title", feed_config.get("title", app.get("title", config_path.stem))),
        "channel_hint": entry.get("channel_hint", ""),
        "rss_url": feed_config.get("feed_url", ""),
        "api_url": feed_config.get("api_url", ""),
        "count": len(items),
        "errors": errors,
    }


def write_index(path: Path, manifest: dict[str, Any], generated: list[dict[str, Any]]) -> None:
    site = manifest.get("site", {})
    title = html.escape(site.get("title", "Router / Network News Feeds"))
    description = html.escape(site.get("description", "Slackチャンネル別RSSフィードです。"))
    generated_at = news_to_slack.display_datetime(
        dt.datetime.now(dt.timezone.utc),
        site.get("timezone", "Asia/Tokyo"),
    )
    rows = []
    for item in generated:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item['title']))}</td>"
            f"<td><code>{html.escape(str(item.get('channel_hint', '')))}</code></td>"
            f"<td><a href=\"{html.escape(str(item['rss_url']))}\">RSS</a></td>"
            f"<td><a href=\"{html.escape(str(item['api_url']))}\">JSON</a></td>"
            f"<td>{int(item['count'])}</td>"
            "</tr>"
        )
    body_rows = "\n      ".join(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 960px; margin: 48px auto; padding: 0 20px; line-height: 1.7; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 24px; }}
    th, td {{ border-bottom: 1px solid #d8dee6; padding: 10px 8px; text-align: left; vertical-align: top; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
    a {{ color: #0f5bd8; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{description}</p>
  <p>過去記事リスト: <a href="archive/">archive/</a></p>
  <table>
    <thead>
      <tr><th>ジャンル</th><th>Slackチャンネル例</th><th>RSS</th><th>JSON</th><th>件数</th></tr>
    </thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
  <p>最終生成: {html.escape(generated_at)}</p>
</body>
</html>
""",
        encoding="utf-8",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate all configured RSS and JSON feeds.")
    parser.add_argument("--manifest", default="config/feeds.json", help="feed manifest path")
    parser.add_argument("--index-output", default="docs/index.html", help="index HTML output path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    project_root = project_root_for_manifest(manifest_path)
    generated = [generate_one(entry, project_root) for entry in manifest["feeds"]]
    write_index(resolve_path(project_root, args.index_output), manifest, generated)
    for item in generated:
        print(f"{item['id']}: {item['count']} 件 -> {item['rss_url']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
