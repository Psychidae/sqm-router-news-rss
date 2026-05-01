#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DEFAULT_CONFIG = "config/japan-market.json"
SQM_NS = "https://psychidae.github.io/sqm-router-news-rss/ns"
ET.register_namespace("sqm", SQM_NS)


@dataclasses.dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    summary: str
    published: dt.datetime | None
    source: str
    source_url: str
    item_id: str
    info_type: str = "未分類"
    score: int = 0


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if local_name(child.tag) == name]


def first_child(element: ET.Element, names: Iterable[str]) -> ET.Element | None:
    wanted = set(names)
    for child in list(element):
        if local_name(child.tag) in wanted:
            return child
    return None


def element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        filtered = [
            (key, value)
            for key, value in query
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        ]
        return urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urllib.parse.urlencode(filtered, doseq=True),
                "",
            )
        )
    except ValueError:
        return url.strip()


def stable_item_id(source: str, title: str, link: str, published: dt.datetime | None) -> str:
    seed = normalize_url(link) or f"{source}\n{title}\n{published.isoformat() if published else ''}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def parse_feed(xml_bytes: bytes, source_name: str, source_url: str) -> list[NewsItem]:
    root = ET.fromstring(xml_bytes)
    root_name = local_name(root.tag).lower()
    if root_name == "rss":
        channel = first_child(root, ["channel"]) or root
        entries = children(channel, "item")
        return [parse_rss_item(entry, source_name, source_url) for entry in entries]
    if root_name == "feed":
        return [parse_atom_entry(entry, source_name, source_url) for entry in children(root, "entry")]
    if root_name == "rdf":
        return [parse_rss_item(entry, source_name, source_url) for entry in children(root, "item")]
    raise ValueError(f"unsupported feed format: {root_name}")


def parse_rss_item(entry: ET.Element, source_name: str, source_url: str) -> NewsItem:
    title = clean_text(element_text(first_child(entry, ["title"])))
    link = clean_text(element_text(first_child(entry, ["link"])))
    guid = clean_text(element_text(first_child(entry, ["guid", "id"])))
    summary = clean_text(element_text(first_child(entry, ["description", "summary", "encoded"])))
    date_value = element_text(first_child(entry, ["pubDate", "published", "updated", "date"]))
    published = parse_datetime(date_value)
    final_link = link or guid
    return NewsItem(
        title=title or "(no title)",
        link=final_link,
        summary=summary,
        published=published,
        source=source_name,
        source_url=source_url,
        item_id=stable_item_id(source_name, title, final_link, published),
    )


def parse_atom_entry(entry: ET.Element, source_name: str, source_url: str) -> NewsItem:
    title = clean_text(element_text(first_child(entry, ["title"])))
    link = ""
    for link_node in children(entry, "link"):
        rel = link_node.attrib.get("rel", "alternate")
        href = link_node.attrib.get("href", "")
        if href and rel in {"alternate", ""}:
            link = href
            break
    if not link:
        link = clean_text(element_text(first_child(entry, ["id"])))
    summary = clean_text(element_text(first_child(entry, ["summary", "content"])))
    date_value = element_text(first_child(entry, ["published", "updated"]))
    published = parse_datetime(date_value)
    return NewsItem(
        title=title or "(no title)",
        link=link,
        summary=summary,
        published=published,
        source=source_name,
        source_url=source_url,
        item_id=stable_item_id(source_name, title, link, published),
    )


def build_url(base_url: str, params: dict[str, Any]) -> str:
    filtered = {key: value for key, value in params.items() if value is not None and value != ""}
    return f"{base_url}?{urllib.parse.urlencode(filtered, doseq=True)}"


def fetch_url(
    url: str,
    user_agent: str,
    timeout: int,
    accept: str = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    headers = {
        "User-Agent": user_agent,
        "Accept": accept,
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(
    url: str,
    user_agent: str,
    timeout: int,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = fetch_url(
        url,
        user_agent=user_agent,
        timeout=timeout,
        accept="application/json, */*;q=0.5",
        extra_headers=extra_headers,
    )
    return json.loads(body.decode("utf-8"))


def datetime_from_timestamp(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def lower_terms(values: Iterable[str]) -> list[str]:
    return [value.lower() for value in values if value]


def term_matches(text: str, term: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    normalized_text = SPACE_RE.sub(" ", text.lower())
    normalized_term = SPACE_RE.sub(" ", term)
    if " " in normalized_term:
        return normalized_term in normalized_text
    pattern = rf"(?<![a-z0-9_-]){re.escape(normalized_term)}(?![a-z0-9_-])"
    return re.search(pattern, normalized_text) is not None


def item_search_text(item: NewsItem) -> str:
    return f"{item.title}\n{item.summary}".lower()


def matches_terms(text: str, terms: Iterable[str]) -> bool:
    term_list = [term for term in terms if term]
    return not term_list or any(term_matches(text, term) for term in term_list)


def has_excluded_term(text: str, terms: Iterable[str]) -> bool:
    return any(term_matches(text, term) for term in terms)


def score_item(item: NewsItem, score_terms: Iterable[str], now: dt.datetime) -> int:
    title = item.title.lower()
    summary = item.summary.lower()
    source = item.source.lower()
    score = 0
    for term in score_terms:
        if term_matches(title, term):
            score += 5
        elif term_matches(summary, term):
            score += 2
        elif term_matches(source, term):
            score += 1
    if item.published:
        age_hours = max(0.0, (now - item.published).total_seconds() / 3600)
        if age_hours <= 24:
            score += 3
        elif age_hours <= 72:
            score += 2
        elif age_hours <= 168:
            score += 1
    return score


def default_info_type(source: dict[str, Any]) -> str:
    source_type = source.get("type", "rss")
    name = str(source.get("name", ""))
    if source.get("info_type"):
        return str(source["info_type"])
    if source_type == "github_search":
        return "開発一次情報"
    if source_type == "reddit_search":
        return "相談投稿"
    if source_type == "hackernews_search":
        return "技術コミュニティ"
    if source_type == "google_news" or name.startswith("Bing News"):
        return "ニュース検索"
    if source_type == "brave_search":
        return "全Webニュース検索" if source.get("search_type") == "news" else "全Web検索"
    if "OpenWrt releases" in name or "OpenWrt site" in name or "Bufferbloat.net" in name:
        return "公式一次情報"
    if "forum" in name.lower():
        return "公式フォーラム"
    return "RSS/Atom"


def attach_info_type(items: list[NewsItem], info_type: str) -> list[NewsItem]:
    return [dataclasses.replace(item, info_type=info_type) for item in items]


def fetch_rss_source(source: dict[str, Any], name: str, user_agent: str, timeout: int) -> list[NewsItem]:
    url = str(source["url"])
    body = fetch_url(url, user_agent=user_agent, timeout=timeout)
    return parse_feed(body, name, url)


def fetch_google_news_source(source: dict[str, Any], name: str, user_agent: str, timeout: int) -> list[NewsItem]:
    url = build_url(
        "https://news.google.com/rss/search",
        {
            "q": source["query"],
            "hl": source.get("hl", "ja"),
            "gl": source.get("gl", "JP"),
            "ceid": source.get("ceid", "JP:ja"),
        },
    )
    body = fetch_url(url, user_agent=user_agent, timeout=timeout)
    return parse_feed(body, name, url)


def fetch_hackernews_source(source: dict[str, Any], name: str, user_agent: str, timeout: int) -> list[NewsItem]:
    url = build_url(
        "https://hn.algolia.com/api/v1/search_by_date",
        {
            "query": source["query"],
            "tags": source.get("tags", "story"),
            "hitsPerPage": int(source.get("limit", 20)),
        },
    )
    data = fetch_json(url, user_agent=user_agent, timeout=timeout)
    items: list[NewsItem] = []
    for hit in data.get("hits", []):
        title = clean_text(hit.get("title") or hit.get("story_title") or hit.get("comment_text") or "")
        object_id = str(hit.get("objectID", ""))
        link = hit.get("url") or hit.get("story_url") or (f"https://news.ycombinator.com/item?id={object_id}" if object_id else "")
        summary_parts = [
            clean_text(hit.get("story_text") or hit.get("comment_text") or ""),
            f"author: {hit.get('author')}" if hit.get("author") else "",
            f"points: {hit.get('points')}" if hit.get("points") is not None else "",
        ]
        summary = " / ".join(part for part in summary_parts if part)
        published = parse_datetime(hit.get("created_at"))
        items.append(
            NewsItem(
                title=title or "(no title)",
                link=link,
                summary=summary,
                published=published,
                source=name,
                source_url=url,
                item_id=stable_item_id(name, title, link, published),
            )
        )
    return items


def fetch_reddit_source(source: dict[str, Any], name: str, user_agent: str, timeout: int) -> list[NewsItem]:
    subreddit = source.get("subreddit")
    base_url = f"https://www.reddit.com/r/{subreddit}/search.json" if subreddit else "https://www.reddit.com/search.json"
    url = build_url(
        base_url,
        {
            "q": source["query"],
            "sort": source.get("sort", "new"),
            "t": source.get("time", "month"),
            "limit": int(source.get("limit", 25)),
            "restrict_sr": "true" if subreddit else "false",
            "raw_json": 1,
        },
    )
    data = fetch_json(url, user_agent=user_agent, timeout=timeout)
    items: list[NewsItem] = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = clean_text(post.get("title"))
        permalink = post.get("permalink", "")
        link = post.get("url_overridden_by_dest") or (f"https://www.reddit.com{permalink}" if permalink else "")
        summary = clean_text(post.get("selftext") or post.get("link_flair_text") or post.get("domain") or "")
        published = datetime_from_timestamp(post.get("created_utc"))
        post_source = f"{name} / r/{post.get('subreddit')}" if post.get("subreddit") else name
        items.append(
            NewsItem(
                title=title or "(no title)",
                link=link,
                summary=summary,
                published=published,
                source=post_source,
                source_url=url,
                item_id=stable_item_id(post_source, title, link, published),
            )
        )
    return items


def fetch_github_source(source: dict[str, Any], name: str, user_agent: str, timeout: int) -> list[NewsItem]:
    endpoint = source.get("endpoint", "issues")
    if endpoint not in {"issues", "repositories"}:
        raise ValueError(f"unsupported GitHub search endpoint: {endpoint}")
    url = build_url(
        f"https://api.github.com/search/{endpoint}",
        {
            "q": source["query"],
            "sort": source.get("sort", "updated" if endpoint == "issues" else "updated"),
            "order": source.get("order", "desc"),
            "per_page": int(source.get("limit", 20)),
        },
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token_env = source.get("token_env", "GITHUB_TOKEN")
    token = os.environ.get(token_env)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = fetch_json(url, user_agent=user_agent, timeout=timeout, extra_headers=headers)
    items: list[NewsItem] = []
    for raw in data.get("items", []):
        if endpoint == "repositories":
            title = clean_text(raw.get("full_name") or raw.get("name"))
            link = raw.get("html_url", "")
            summary = clean_text(raw.get("description") or "")
            published = parse_datetime(raw.get("pushed_at") or raw.get("updated_at") or raw.get("created_at"))
            item_source = name
        else:
            title = clean_text(raw.get("title"))
            link = raw.get("html_url", "")
            summary = clean_text(raw.get("body") or "")
            published = parse_datetime(raw.get("updated_at") or raw.get("created_at"))
            repo_url = raw.get("repository_url", "")
            repo = repo_url.rsplit("/repos/", 1)[-1] if "/repos/" in repo_url else ""
            item_source = f"{name} / {repo}" if repo else name
        items.append(
            NewsItem(
                title=title or "(no title)",
                link=link,
                summary=summary,
                published=published,
                source=item_source,
                source_url=url,
                item_id=stable_item_id(item_source, title, link, published),
            )
        )
    return items


def fetch_brave_source(source: dict[str, Any], name: str, user_agent: str, timeout: int) -> list[NewsItem]:
    api_key_env = source.get("api_key_env", "BRAVE_SEARCH_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return []
    search_type = source.get("search_type", "web")
    if search_type not in {"web", "news"}:
        raise ValueError(f"unsupported Brave search_type: {search_type}")
    url = build_url(
        f"https://api.search.brave.com/res/v1/{search_type}/search",
        {
            "q": source["query"],
            "count": int(source.get("limit", 10)),
            "country": source.get("country", "jp"),
            "search_lang": source.get("search_lang", "en"),
            "freshness": source.get("freshness"),
            "spellcheck": int(source.get("spellcheck", 1)),
        },
    )
    data = fetch_json(
        url,
        user_agent=user_agent,
        timeout=timeout,
        extra_headers={"X-Subscription-Token": api_key},
    )
    raw_items = data.get("web", {}).get("results", []) if search_type == "web" else data.get("results", [])
    items: list[NewsItem] = []
    for raw in raw_items:
        title = clean_text(raw.get("title"))
        link = raw.get("url", "")
        snippets = raw.get("extra_snippets") or []
        summary = clean_text(" ".join([raw.get("description", ""), *snippets]))
        published = parse_datetime(raw.get("page_age") or raw.get("age"))
        item_source = f"{name} / {raw.get('profile', {}).get('name')}" if raw.get("profile", {}).get("name") else name
        items.append(
            NewsItem(
                title=title or "(no title)",
                link=link,
                summary=summary,
                published=published,
                source=item_source,
                source_url=url,
                item_id=stable_item_id(item_source, title, link, published),
            )
        )
    return items


def fetch_source(source: dict[str, Any], user_agent: str, timeout: int) -> list[NewsItem]:
    source_type = source.get("type", "rss")
    name = str(source.get("name", source.get("url", source.get("query", "unknown source"))))
    info_type = default_info_type(source)
    if source_type in {"rss", "atom"}:
        return attach_info_type(fetch_rss_source(source, name, user_agent, timeout), info_type)
    if source_type == "google_news":
        return attach_info_type(fetch_google_news_source(source, name, user_agent, timeout), info_type)
    if source_type == "hackernews_search":
        return attach_info_type(fetch_hackernews_source(source, name, user_agent, timeout), info_type)
    if source_type == "reddit_search":
        return attach_info_type(fetch_reddit_source(source, name, user_agent, timeout), info_type)
    if source_type == "github_search":
        return attach_info_type(fetch_github_source(source, name, user_agent, timeout), info_type)
    if source_type == "brave_search":
        return attach_info_type(fetch_brave_source(source, name, user_agent, timeout), info_type)
    raise ValueError(f"unsupported source type: {source_type}")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config.get("sources"), list) or not config["sources"]:
        raise ValueError("config must contain a non-empty sources list")
    return config


def project_root_for_config(config_path: Path) -> Path:
    parent = config_path.resolve().parent
    if parent.name == "config":
        return parent.parent
    return parent


def resolve_project_path(project_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def load_dotenv(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def infer_github_pages_url() -> str:
    explicit = os.environ.get("PAGES_SITE_URL")
    if explicit:
        return explicit.rstrip("/") + "/"
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repository:
        return ""
    owner, repo = repository.split("/", 1)
    owner = owner.lower()
    repo = repo.lower()
    if repo == f"{owner}.github.io":
        return f"https://{owner}.github.io/"
    return f"https://{owner}.github.io/{repo}/"


def apply_runtime_feed_defaults(config: dict[str, Any]) -> None:
    feed = config.setdefault("feed", {})
    if not feed.get("site_url"):
        inferred = infer_github_pages_url()
        if inferred:
            feed["site_url"] = inferred
    if not feed.get("feed_url") and feed.get("site_url"):
        feed["feed_url"] = absolutize_url(str(feed["site_url"]), "feed.xml")


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"sent_ids": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"sent_ids": {}}
    if not isinstance(data.get("sent_ids"), dict):
        data["sent_ids"] = {}
    return data


def save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def prune_state(state: dict[str, Any], now: dt.datetime, retention_days: int) -> None:
    cutoff = now - dt.timedelta(days=retention_days)
    kept: dict[str, str] = {}
    for item_id, sent_at in state.get("sent_ids", {}).items():
        parsed = parse_datetime(str(sent_at))
        if parsed is None or parsed >= cutoff:
            kept[item_id] = str(sent_at)
    state["sent_ids"] = kept


def collect_items(config: dict[str, Any], ignore_state: bool = False) -> tuple[list[NewsItem], list[str], dict[str, Any], Path | None]:
    app = config.get("app", {})
    filters = config.get("filters", {})
    project_root = Path(config["_project_root"])
    state_path = resolve_project_path(project_root, app.get("state_path", ".state/sent_items.json"))
    state = load_state(state_path)

    now = dt.datetime.now(dt.timezone.utc)
    prune_state(state, now, int(app.get("state_retention_days", 60)))
    lookback_hours = int(app.get("lookback_hours", 168))
    cutoff = now - dt.timedelta(hours=lookback_hours)
    timeout = int(app.get("request_timeout_seconds", 20))
    user_agent = app.get("user_agent", "sqm-router-news-slack/0.1")
    excluded = filters.get("exclude_any", [])
    score_terms = filters.get("score_terms", filters.get("required_any", []))

    items: list[NewsItem] = []
    errors: list[str] = []
    seen_this_run: set[str] = set()

    for source in config["sources"]:
        if source.get("enabled") is False:
            continue
        name = str(source.get("name", source.get("url", source.get("query", "unknown source"))))
        try:
            parsed_items = fetch_source(source, user_agent=user_agent, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: {exc}")
            continue

        required = source.get("required_any", filters.get("required_any", []))
        for item in parsed_items:
            text = item_search_text(item)
            if item.published is not None and item.published < cutoff:
                continue
            if has_excluded_term(text, excluded):
                continue
            if not matches_terms(text, required):
                continue
            if item.item_id in seen_this_run:
                continue
            if not ignore_state and item.item_id in state.get("sent_ids", {}):
                continue
            seen_this_run.add(item.item_id)
            items.append(dataclasses.replace(item, score=score_item(item, score_terms, now)))

    items.sort(key=lambda item: (item.score, item.published or dt.datetime.min.replace(tzinfo=dt.timezone.utc)), reverse=True)
    max_items = int(app.get("max_items", 8))
    return items[:max_items], errors, state, state_path


def slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def display_datetime(value: dt.datetime | None, timezone_name: str) -> str:
    if value is None:
        return "日時不明"
    if ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(timezone_name)
            value = value.astimezone(local_tz)
        except Exception:
            value = value.astimezone(dt.timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M")


def rfc2822(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return email.utils.format_datetime(current.astimezone(dt.timezone.utc), usegmt=True)


def absolutize_url(base_url: str, value: str) -> str:
    if not base_url:
        return value
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))


def item_description(item: NewsItem, max_chars: int, timezone_name: str = "Asia/Tokyo") -> str:
    parts = [
        f"種別: {item.info_type}",
        f"ソース: {item.source}",
    ]
    if item.published is not None:
        parts.append(f"元記事日付: {display_datetime(item.published, timezone_name)}")
    if item.summary:
        parts.append(truncate(item.summary, max_chars))
    return "\n".join(parts)


def load_existing_rss_first_seen_dates(path: Path | None) -> dict[str, dt.datetime]:
    if path is None or not path.exists():
        return {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}

    channel = first_child(root, ["channel"]) or root
    dates: dict[str, dt.datetime] = {}
    for entry in children(channel, "item"):
        item_id = clean_text(element_text(first_child(entry, ["guid", "id"])))
        first_seen = parse_datetime(element_text(first_child(entry, ["firstSeenDate", "firstSeen"])))
        if item_id and first_seen is not None:
            dates[item_id] = first_seen
    return dates


def resolve_rss_item_dates(
    items: list[NewsItem],
    config: dict[str, Any],
    existing_first_seen_dates: dict[str, dt.datetime],
    now: dt.datetime,
) -> dict[str, dt.datetime]:
    feed_config = config.get("feed", {})
    if feed_config.get("item_date_mode") != "first_seen":
        return {}
    return {
        item.item_id: existing_first_seen_dates.get(item.item_id, now)
        for item in items
    }


def build_rss_feed(
    items: list[NewsItem],
    config: dict[str, Any],
    rss_item_dates: dict[str, dt.datetime] | None = None,
) -> bytes:
    app = config.get("app", {})
    feed_config = config.get("feed", {})
    title = feed_config.get("title", app.get("title", "SQMルーター最新情報"))
    description = feed_config.get(
        "description",
        "SQMルーター、OpenWrt SQM、bufferbloat、CAKE関連の最新情報",
    )
    site_url = feed_config.get("site_url", "")
    feed_url = feed_config.get("feed_url", absolutize_url(site_url, "feed.xml") if site_url else "")
    language = feed_config.get("language", "ja")
    summary_chars = int(app.get("summary_chars", 180))
    timezone_name = app.get("timezone", "Asia/Tokyo")
    generated_at = dt.datetime.now(dt.timezone.utc)
    if rss_item_dates is None and feed_config.get("item_date_mode") == "first_seen":
        rss_item_dates = {item.item_id: generated_at for item in items}
    rss_item_dates = rss_item_dates or {}

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = site_url or feed_url or "feed.xml"
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "language").text = language
    ET.SubElement(channel, "lastBuildDate").text = rfc2822()
    if feed_url:
        ET.SubElement(channel, "docs").text = feed_url

    for item in items:
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, "title").text = f"[{item.info_type}] {item.title}"
        ET.SubElement(entry, "link").text = item.link or item.source_url
        guid = ET.SubElement(entry, "guid", {"isPermaLink": "false"})
        guid.text = item.item_id
        ET.SubElement(entry, "description").text = item_description(item, summary_chars, timezone_name)
        rss_date = rss_item_dates.get(item.item_id)
        ET.SubElement(entry, "pubDate").text = rfc2822(rss_date or item.published)
        if rss_date is not None:
            ET.SubElement(entry, f"{{{SQM_NS}}}firstSeenDate").text = iso_datetime(rss_date)
            if item.published is not None:
                ET.SubElement(entry, f"{{{SQM_NS}}}sourcePublishedDate").text = iso_datetime(item.published)
        ET.SubElement(entry, "source", {"url": item.source_url}).text = item.source
        ET.SubElement(entry, "category").text = item.info_type

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def write_rss_feed(
    path: Path,
    items: list[NewsItem],
    config: dict[str, Any],
    rss_item_dates: dict[str, dt.datetime] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_rss_feed(items, config, rss_item_dates))


def iso_datetime(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def item_to_api_dict(item: NewsItem, summary_chars: int) -> dict[str, Any]:
    return {
        "id": item.item_id,
        "title": item.title,
        "url": item.link,
        "summary": truncate(item.summary, summary_chars),
        "published_at": iso_datetime(item.published),
        "source": item.source,
        "source_url": item.source_url,
        "info_type": item.info_type,
        "score": item.score,
    }


def build_json_payload(items: list[NewsItem], config: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    app = config.get("app", {})
    feed_config = config.get("feed", {})
    site_url = feed_config.get("site_url", "")
    feed_url = feed_config.get("feed_url", absolutize_url(site_url, "feed.xml") if site_url else "")
    api_url = feed_config.get("api_url", absolutize_url(site_url, "api/latest.json") if site_url else "api/latest.json")
    summary_chars = int(app.get("summary_chars", 180))
    return {
        "title": feed_config.get("title", app.get("title", "SQMルーター最新情報")),
        "description": feed_config.get(
            "description",
            "SQMルーター、OpenWrt SQM、bufferbloat、CAKE関連の最新情報",
        ),
        "generated_at": iso_datetime(dt.datetime.now(dt.timezone.utc)),
        "site_url": site_url,
        "feed_url": feed_url,
        "api_url": api_url,
        "count": len(items),
        "errors": errors,
        "items": [item_to_api_dict(item, summary_chars) for item in items],
    }


def write_json_api(path: Path, items: list[NewsItem], config: dict[str, Any], errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(build_json_payload(items, config, errors), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_index_html(config: dict[str, Any]) -> str:
    app = config.get("app", {})
    feed_config = config.get("feed", {})
    title = html.escape(feed_config.get("title", app.get("title", "SQMルーター最新情報")))
    description = html.escape(
        feed_config.get("description", "SQMルーター関連の最新情報を配信するRSSフィードです。")
    )
    site_url = feed_config.get("site_url", "")
    api_href = html.escape(feed_config.get("api_url", absolutize_url(site_url, "api/latest.json") if site_url else "api/latest.json"))
    updated = html.escape(display_datetime(dt.datetime.now(dt.timezone.utc), app.get("timezone", "Asia/Tokyo")))
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="alternate" type="application/rss+xml" title="{title}" href="feed.xml">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 760px; margin: 48px auto; padding: 0 20px; line-height: 1.7; color: #1f2933; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
    a {{ color: #0f5bd8; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{description}</p>
  <p>このページ、RSS、JSON APIはGitHubログインなしで利用できます。</p>
  <p>RSS URL: <a href="feed.xml"><code>feed.xml</code></a></p>
  <p>JSON API: <a href="{api_href}"><code>api/latest.json</code></a></p>
  <p>最終生成: {updated}</p>
</body>
</html>
"""


def write_index_html(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_index_html(config), encoding="utf-8")


def build_slack_text(items: list[NewsItem], config: dict[str, Any], errors: list[str]) -> str:
    app = config.get("app", {})
    title = app.get("title", "ニュース通知")
    timezone_name = app.get("timezone", "Asia/Tokyo")
    summary_chars = int(app.get("summary_chars", 180))
    now_text = display_datetime(dt.datetime.now(dt.timezone.utc), timezone_name)

    if not items:
        lines = [f"*{slack_escape(title)}*", f"{now_text} 時点で新着はありません。"]
    else:
        lines = [f"*{slack_escape(title)}*", f"{now_text} 時点の新着 {len(items)} 件"]
        type_counts: dict[str, int] = {}
        for item in items:
            type_counts[item.info_type] = type_counts.get(item.info_type, 0) + 1
        if type_counts:
            breakdown = " / ".join(f"{slack_escape(key)} {value}" for key, value in sorted(type_counts.items()))
            lines.append(f"内訳: {breakdown}")
        for index, item in enumerate(items, start=1):
            date_text = display_datetime(item.published, timezone_name)
            safe_title = slack_escape(item.title).replace("|", " ")
            if item.link:
                headline = f"<{item.link}|{safe_title}>"
            else:
                headline = safe_title
            lines.append(f"{index}. {headline}")
            info_label = slack_escape(item.info_type)
            lines.append(f"   [{info_label}] {slack_escape(item.source)} / {date_text}")
            if item.summary:
                lines.append(f"   {slack_escape(truncate(item.summary, summary_chars))}")

    if errors:
        lines.append("")
        lines.append(f"_取得失敗: {len(errors)} ソース。ログで確認してください。_")
    return "\n".join(lines)


def post_to_slack(webhook_url: str, text: str, username: str | None = None) -> None:
    payload: dict[str, Any] = {"text": text, "mrkdwn": True}
    if username:
        payload["username"] = username
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Slack webhook returned HTTP {response.status}")


def mark_sent(state: dict[str, Any], items: list[NewsItem]) -> None:
    sent_ids = state.setdefault("sent_ids", {})
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for item in items:
        sent_ids[item.item_id] = now
    state["last_run"] = now


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch SQM router news and publish it as RSS or send it to Slack.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"config file path (default: {DEFAULT_CONFIG})")
    parser.add_argument("--dry-run", action="store_true", help="print the Slack message without posting or updating state")
    parser.add_argument("--ignore-state", action="store_true", help="ignore previously sent items")
    parser.add_argument("--env-file", help="optional .env file path; defaults to app.env_file in config")
    parser.add_argument("--lookback-hours", type=int, help="override app.lookback_hours")
    parser.add_argument("--limit", type=int, help="override app.max_items")
    parser.add_argument("--rss-output", help="write RSS feed to this path instead of sending to Slack")
    parser.add_argument("--json-output", help="write JSON API payload to this path instead of sending to Slack")
    parser.add_argument("--index-output", help="write a simple HTML landing page for the RSS feed")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    project_root = project_root_for_config(config_path)
    config["_project_root"] = str(project_root)

    app = config.setdefault("app", {})
    if args.lookback_hours is not None:
        app["lookback_hours"] = args.lookback_hours
    if args.limit is not None:
        app["max_items"] = args.limit

    env_file_value = args.env_file if args.env_file else app.get("env_file", ".env")
    load_dotenv(resolve_project_path(project_root, env_file_value))
    apply_runtime_feed_defaults(config)

    publishing_files = bool(args.rss_output or args.json_output)
    items, errors, state, state_path = collect_items(config, ignore_state=args.ignore_state or publishing_files)
    text = build_slack_text(items, config, errors)

    for error in errors:
        print(f"warning: {error}", file=sys.stderr)

    if publishing_files:
        rss_path = resolve_project_path(project_root, args.rss_output)
        json_path = resolve_project_path(project_root, args.json_output)
        preserve_existing = bool(config.get("feed", {}).get("preserve_existing_on_error", True))
        existing_paths = [path for path in [rss_path, json_path] if path is not None and path.exists()]
        if preserve_existing and errors and not items and existing_paths:
            kept = ", ".join(str(path) for path in existing_paths)
            print(f"取得エラーがあり新規項目が0件のため、既存公開ファイルを保持しました: {kept}")
            return 0
        if rss_path is not None:
            existing_first_seen_dates = load_existing_rss_first_seen_dates(rss_path)
            rss_item_dates = resolve_rss_item_dates(
                items,
                config,
                existing_first_seen_dates,
                dt.datetime.now(dt.timezone.utc),
            )
            write_rss_feed(rss_path, items, config, rss_item_dates)
        if json_path is not None:
            write_json_api(json_path, items, config, errors)
        if args.index_output:
            index_path = resolve_project_path(project_root, args.index_output)
            if index_path is not None:
                write_index_html(index_path, config)
        outputs = ", ".join(str(path) for path in [rss_path, json_path] if path is not None)
        print(f"公開ファイルを生成しました: {outputs} ({len(items)} 件)")
        return 0

    send_empty = bool(app.get("send_empty", False))
    if args.dry_run:
        print(text)
        return 0

    if not items and not send_empty:
        print("新着がないためSlack送信をスキップしました。")
        state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save_state(state_path, state)
        return 0

    slack = config.get("slack", {})
    webhook_env = slack.get("webhook_env", "SLACK_WEBHOOK_URL")
    webhook_url = os.environ.get(webhook_env)
    if not webhook_url:
        raise RuntimeError(f"environment variable {webhook_env} is not set")

    post_to_slack(webhook_url, text, username=slack.get("username"))
    mark_sent(state, items)
    save_state(state_path, state)
    print(f"Slackへ {len(items)} 件を送信しました。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
