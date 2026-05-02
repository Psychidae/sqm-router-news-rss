import datetime as dt
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "news_to_slack.py"
SPEC = importlib.util.spec_from_file_location("news_to_slack", MODULE_PATH)
news_to_slack = importlib.util.module_from_spec(SPEC)
sys.modules["news_to_slack"] = news_to_slack
SPEC.loader.exec_module(news_to_slack)

ARCHIVE_PATH = Path(__file__).resolve().parents[1] / "src" / "build_archive.py"
ARCHIVE_SPEC = importlib.util.spec_from_file_location("build_archive", ARCHIVE_PATH)
build_archive = importlib.util.module_from_spec(ARCHIVE_SPEC)
sys.modules["build_archive"] = build_archive
ARCHIVE_SPEC.loader.exec_module(build_archive)


class NewsToSlackTests(unittest.TestCase):
    def test_parse_rss_item(self):
        feed = b"""<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>OpenWrt SQM update</title>
              <link>https://example.com/sqm?utm_source=test</link>
              <description>CAKE and fq_codel notes</description>
              <pubDate>Fri, 01 May 2026 08:00:00 +0000</pubDate>
            </item>
          </channel>
        </rss>
        """
        items = news_to_slack.parse_feed(feed, "Example", "https://example.com/feed.xml")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "OpenWrt SQM update")
        self.assertIn("CAKE", items[0].summary)
        self.assertEqual(items[0].published.tzinfo, dt.timezone.utc)

    def test_parse_atom_entry(self):
        feed = b"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Bufferbloat router release</title>
            <link href="https://example.com/atom-item" />
            <summary>Smart Queue Management details</summary>
            <updated>2026-05-01T09:30:00Z</updated>
          </entry>
        </feed>
        """
        items = news_to_slack.parse_feed(feed, "Atom", "https://example.com/atom.xml")
        self.assertEqual(items[0].link, "https://example.com/atom-item")
        self.assertEqual(items[0].published.hour, 9)

    def test_term_filtering(self):
        item = news_to_slack.NewsItem(
            title="Router firmware release",
            link="https://example.com",
            summary="Includes Smart Queue Management improvements.",
            published=None,
            source="Example",
            source_url="https://example.com/feed.xml",
            item_id="abc",
        )
        text = news_to_slack.item_search_text(item)
        self.assertTrue(news_to_slack.matches_terms(text, ["SQM", "Smart Queue Management"]))
        self.assertFalse(news_to_slack.has_excluded_term(text, ["earnings call"]))

    def test_short_terms_do_not_match_inside_words(self):
        text = "dnsmasq settings and a piece-of-cake setup"
        self.assertFalse(news_to_slack.matches_terms(text, ["SQM"]))
        self.assertFalse(news_to_slack.matches_terms(text, ["CAKE"]))
        self.assertTrue(news_to_slack.matches_terms("CAKE QoS update", ["CAKE"]))

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sent_items.json"
            state = {"sent_ids": {"abc": "2026-05-01T00:00:00+00:00"}}
            news_to_slack.save_state(path, state)
            loaded = news_to_slack.load_state(path)
            self.assertEqual(loaded["sent_ids"]["abc"], "2026-05-01T00:00:00+00:00")

    def test_hackernews_search_parser(self):
        old_fetch_json = news_to_slack.fetch_json
        try:
            news_to_slack.fetch_json = lambda *args, **kwargs: {
                "hits": [
                    {
                        "title": "Bufferbloat update",
                        "url": "https://example.com/bufferbloat",
                        "created_at": "2026-05-01T00:00:00Z",
                        "author": "tester",
                        "points": 10,
                    }
                ]
            }
            items = news_to_slack.fetch_hackernews_source(
                {"query": "bufferbloat", "limit": 1}, "HN", "agent", 5
            )
        finally:
            news_to_slack.fetch_json = old_fetch_json
        self.assertEqual(items[0].title, "Bufferbloat update")
        self.assertIn("points: 10", items[0].summary)

    def test_reddit_search_parser(self):
        old_fetch_json = news_to_slack.fetch_json
        try:
            news_to_slack.fetch_json = lambda *args, **kwargs: {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "OpenWrt SQM help",
                                "url_overridden_by_dest": "https://example.com/reddit",
                                "selftext": "Smart Queue Management",
                                "created_utc": 1777593600,
                                "subreddit": "openwrt",
                            }
                        }
                    ]
                }
            }
            items = news_to_slack.fetch_reddit_source(
                {"query": "OpenWrt SQM", "limit": 1}, "Reddit", "agent", 5
            )
        finally:
            news_to_slack.fetch_json = old_fetch_json
        self.assertEqual(items[0].source, "Reddit / r/openwrt")
        self.assertEqual(items[0].published.tzinfo, dt.timezone.utc)

    def test_brave_source_skips_without_api_key(self):
        old_value = os.environ.pop("BRAVE_SEARCH_API_KEY", None)
        try:
            items = news_to_slack.fetch_brave_source(
                {"query": "SQM router", "search_type": "web"}, "Brave", "agent", 5
            )
        finally:
            if old_value is not None:
                os.environ["BRAVE_SEARCH_API_KEY"] = old_value
        self.assertEqual(items, [])

    def test_default_info_type(self):
        self.assertEqual(news_to_slack.default_info_type({"type": "github_search"}), "開発一次情報")
        self.assertEqual(news_to_slack.default_info_type({"type": "reddit_search"}), "相談投稿")
        self.assertEqual(
            news_to_slack.default_info_type({"type": "brave_search", "search_type": "web"}),
            "全Web検索",
        )
        self.assertEqual(
            news_to_slack.default_info_type({"type": "google_news"}),
            "ニュース検索",
        )

    def test_slack_text_includes_info_type(self):
        item = news_to_slack.NewsItem(
            title="SQM issue",
            link="https://example.com/issue",
            summary="cake_mq regression",
            published=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
            source="GitHub issues",
            source_url="https://api.github.com/search/issues",
            item_id="abc",
            info_type="開発一次情報",
        )
        text = news_to_slack.build_slack_text([item], {"app": {"timezone": "Asia/Tokyo"}}, [])
        self.assertIn("内訳: 開発一次情報 1", text)
        self.assertIn("[開発一次情報] GitHub issues", text)

    def test_build_rss_feed_includes_info_type(self):
        item = news_to_slack.NewsItem(
            title="SQM issue",
            link="https://example.com/issue",
            summary="cake_mq regression",
            published=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
            source="GitHub issues",
            source_url="https://api.github.com/search/issues",
            item_id="abc",
            info_type="開発一次情報",
        )
        body = news_to_slack.build_rss_feed(
            [item],
            {
                "app": {"summary_chars": 120},
                "feed": {"title": "Test Feed", "description": "desc", "language": "ja"},
            },
        ).decode("utf-8")
        self.assertIn("<title>[開発一次情報] SQM issue</title>", body)
        self.assertIn("<category>開発一次情報</category>", body)
        self.assertIn("<guid isPermaLink=\"false\">abc</guid>", body)

    def test_build_rss_feed_can_use_first_seen_date_for_slack(self):
        item = news_to_slack.NewsItem(
            title="SQM issue",
            link="https://example.com/issue",
            summary="cake_mq regression",
            published=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
            source="GitHub issues",
            source_url="https://api.github.com/search/issues",
            item_id="abc",
            info_type="開発一次情報",
        )
        first_seen = dt.datetime(2026, 5, 2, 1, 2, 3, tzinfo=dt.timezone.utc)
        body = news_to_slack.build_rss_feed(
            [item],
            {
                "app": {"summary_chars": 120},
                "feed": {
                    "title": "Test Feed",
                    "description": "desc",
                    "language": "ja",
                    "item_date_mode": "first_seen",
                },
            },
            {"abc": first_seen},
        ).decode("utf-8")
        self.assertIn("<pubDate>Sat, 02 May 2026 01:02:03 GMT</pubDate>", body)
        self.assertIn("<sqm:firstSeenDate>2026-05-02T01:02:03Z</sqm:firstSeenDate>", body)
        self.assertIn("<sqm:sourcePublishedDate>2026-05-01T00:00:00Z</sqm:sourcePublishedDate>", body)

    def test_resolve_rss_item_dates_uses_existing_marked_first_seen(self):
        item = news_to_slack.NewsItem(
            title="SQM issue",
            link="https://example.com/issue",
            summary="cake_mq regression",
            published=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
            source="GitHub issues",
            source_url="https://api.github.com/search/issues",
            item_id="abc",
            info_type="開発一次情報",
        )
        first_seen = dt.datetime(2026, 5, 2, 1, 2, 3, tzinfo=dt.timezone.utc)
        later = dt.datetime(2026, 5, 2, 2, 0, 0, tzinfo=dt.timezone.utc)
        dates = news_to_slack.resolve_rss_item_dates(
            [item],
            {"feed": {"item_date_mode": "first_seen"}},
            {"abc": first_seen},
            later,
        )
        self.assertEqual(dates["abc"], first_seen)

    def test_load_existing_rss_first_seen_dates_ignores_legacy_pubdate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.xml"
            path.write_text(
                """<?xml version='1.0' encoding='utf-8'?>
                <rss version="2.0">
                  <channel>
                    <item>
                      <guid isPermaLink="false">abc</guid>
                      <pubDate>Fri, 01 May 2026 00:00:00 GMT</pubDate>
                    </item>
                  </channel>
                </rss>
                """,
                encoding="utf-8",
            )
            self.assertEqual(news_to_slack.load_existing_rss_first_seen_dates(path), {})

    def test_write_index_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.html"
            news_to_slack.write_index_html(
                path,
                {"app": {"timezone": "Asia/Tokyo"}, "feed": {"title": "Test Feed"}},
            )
            self.assertIn("feed.xml", path.read_text(encoding="utf-8"))
            self.assertIn("api/latest.json", path.read_text(encoding="utf-8"))

    def test_rss_channel_link_defaults_to_feed_xml(self):
        body = news_to_slack.build_rss_feed(
            [],
            {"app": {}, "feed": {"title": "Test Feed", "description": "desc"}},
        ).decode("utf-8")
        self.assertIn("<link>feed.xml</link>", body)

    def test_github_pages_url_inference(self):
        old_repo = os.environ.get("GITHUB_REPOSITORY")
        old_pages = os.environ.get("PAGES_SITE_URL")
        try:
            os.environ.pop("PAGES_SITE_URL", None)
            os.environ["GITHUB_REPOSITORY"] = "octo/sqm-feed"
            config = {"feed": {"site_url": ""}}
            news_to_slack.apply_runtime_feed_defaults(config)
        finally:
            if old_repo is None:
                os.environ.pop("GITHUB_REPOSITORY", None)
            else:
                os.environ["GITHUB_REPOSITORY"] = old_repo
            if old_pages is None:
                os.environ.pop("PAGES_SITE_URL", None)
            else:
                os.environ["PAGES_SITE_URL"] = old_pages
        self.assertEqual(config["feed"]["site_url"], "https://octo.github.io/sqm-feed/")
        self.assertEqual(config["feed"]["feed_url"], "https://octo.github.io/sqm-feed/feed.xml")

    def test_github_pages_url_inference_uses_feed_paths(self):
        old_repo = os.environ.get("GITHUB_REPOSITORY")
        old_pages = os.environ.get("PAGES_SITE_URL")
        try:
            os.environ.pop("PAGES_SITE_URL", None)
            os.environ["GITHUB_REPOSITORY"] = "octo/sqm-feed"
            config = {
                "feed": {
                    "site_url": "",
                    "feed_path": "feeds/router-domestic.xml",
                    "api_path": "api/router-domestic.json",
                }
            }
            news_to_slack.apply_runtime_feed_defaults(config)
        finally:
            if old_repo is None:
                os.environ.pop("GITHUB_REPOSITORY", None)
            else:
                os.environ["GITHUB_REPOSITORY"] = old_repo
            if old_pages is None:
                os.environ.pop("PAGES_SITE_URL", None)
            else:
                os.environ["PAGES_SITE_URL"] = old_pages
        self.assertEqual(config["feed"]["feed_url"], "https://octo.github.io/sqm-feed/feeds/router-domestic.xml")
        self.assertEqual(config["feed"]["api_url"], "https://octo.github.io/sqm-feed/api/router-domestic.json")

    def test_build_json_payload(self):
        item = news_to_slack.NewsItem(
            title="SQM issue",
            link="https://example.com/issue",
            summary="cake_mq regression",
            published=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
            source="GitHub issues",
            source_url="https://api.github.com/search/issues",
            item_id="abc",
            info_type="開発一次情報",
            score=9,
        )
        payload = news_to_slack.build_json_payload(
            [item],
            {
                "app": {"summary_chars": 120},
                "feed": {
                    "title": "Test Feed",
                    "description": "desc",
                    "site_url": "https://example.com/project/",
                    "feed_url": "https://example.com/project/feed.xml",
                },
            },
            [],
        )
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["api_url"], "https://example.com/project/api/latest.json")
        self.assertEqual(payload["items"][0]["id"], "abc")
        self.assertEqual(payload["items"][0]["info_type"], "開発一次情報")

    def test_archive_year_windows(self):
        windows = build_archive.year_windows(
            dt.date(2024, 11, 15),
            dt.date(2026, 2, 3),
        )
        self.assertEqual(
            windows,
            [
                (dt.date(2024, 11, 15), dt.date(2024, 12, 31)),
                (dt.date(2025, 1, 1), dt.date(2025, 12, 31)),
                (dt.date(2026, 1, 1), dt.date(2026, 2, 3)),
            ],
        )

    def test_archive_dedupe_items_prefers_published(self):
        items = build_archive.dedupe_items(
            [
                {"id": "same", "title": "A", "url": "https://example.com/a", "published_at": None},
                {"id": "same", "title": "A", "url": "https://example.com/a", "published_at": "2026-01-01T00:00:00Z"},
            ]
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_at"], "2026-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
