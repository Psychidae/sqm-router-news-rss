# ルーター・ネットワークニュースRSS/API

SQMルーター、国内ルーター市場、海外ルーター、ネットワークセキュリティ、ネットワーク性能・障害のニュースをジャンル別RSS/APIとして生成するシステムです。

Slack API、Incoming Webhook、Slack Appの作成は不要です。Slack側では公開RSS URLをRSS appに登録するだけです。
公開URLの閲覧・API取得・Slack RSS購読にはGitHubログインもPsychidaeアカウントも不要です。

## できること

- Slackチャンネルごとに別RSSを購読できる
- SQM以外のルーター・ネットワークニュースを別フィードに分離
- Google NewsとBrave Searchからジャンル別に取得
- RSS itemのタイトルに `[開発一次情報]` などの情報種別を表示
- Slack RSS appで新着扱いされるよう、RSS itemの日付は「このシステムが初めて見つけた日時」として保持
- GitHub Actionsで全RSS/APIを30分ごとに更新
- GitHub PagesでRSS/JSON APIを公開し、Slack RSS appや自前サーバーから利用
- 過去記事アーカイブをHTML/JSON/CSVで公開
- 広域調査用に `config/sqm-router.json` も残す

## Slackチャンネル別RSS

| 用途 | Slackチャンネル例 | RSS URL | JSON API |
|---|---|---|---|
| SQM国内マーケット | `#sqm-router-news` | `https://psychidae.github.io/sqm-router-news-rss/feed.xml` | `https://psychidae.github.io/sqm-router-news-rss/api/latest.json` |
| 国内ルーター市場 | `#router-domestic` | `https://psychidae.github.io/sqm-router-news-rss/feeds/router-domestic.xml` | `https://psychidae.github.io/sqm-router-news-rss/api/router-domestic.json` |
| 海外ルーターニュース | `#router-global` | `https://psychidae.github.io/sqm-router-news-rss/feeds/router-global.xml` | `https://psychidae.github.io/sqm-router-news-rss/api/router-global.json` |
| ネットワークセキュリティ | `#network-security` | `https://psychidae.github.io/sqm-router-news-rss/feeds/network-security.xml` | `https://psychidae.github.io/sqm-router-news-rss/api/network-security.json` |
| ネットワーク性能・障害 | `#network-performance` | `https://psychidae.github.io/sqm-router-news-rss/feeds/network-performance.xml` | `https://psychidae.github.io/sqm-router-news-rss/api/network-performance.json` |

## 最短セットアップ

1. GitHubにこのフォルダをpushする
2. GitHub Pagesを有効化する
   - `Settings > Pages`
   - `Deploy from a branch`
   - Branch: `main`
   - Folder: `/docs`
3. GitHub Actionsを有効化する
4. 任意で `Settings > Secrets and variables > Actions` に `BRAVE_SEARCH_API_KEY` を登録する
5. Slackの各チャンネルで、上表のRSS URLを登録する

```text
https://psychidae.github.io/sqm-router-news-rss/feed.xml
```

Slack公式手順: [Add RSS feeds to Slack](https://slack.com/hc/en-us/articles/218688467-Add-RSS-feeds-to-Slack)

## ローカルでRSS/APIを生成

```bash
python3 src/generate_all_feeds.py --manifest config/feeds.json --index-output docs/index.html
```

生成後、ブラウザやRSSリーダーで `docs/feed.xml` と `docs/feeds/*.xml`、サーバーから `docs/api/*.json` を確認できます。

## 公開API

```text
GET https://psychidae.github.io/sqm-router-news-rss/api/latest.json
GET https://psychidae.github.io/sqm-router-news-rss/api/router-domestic.json
GET https://psychidae.github.io/sqm-router-news-rss/api/router-global.json
GET https://psychidae.github.io/sqm-router-news-rss/api/network-security.json
GET https://psychidae.github.io/sqm-router-news-rss/api/network-performance.json
```

## 過去記事リスト

過去記事はGitHub Pages上のリストで参照できます。

```text
https://psychidae.github.io/sqm-router-news-rss/archive/
https://psychidae.github.io/sqm-router-news-rss/archive/archive.json
https://psychidae.github.io/sqm-router-news-rss/archive/archive.csv
```

SlackのListを直接作成・更新する権限はこのコネクタにはないため、Slack側には上記URLを固定投稿またはCanvasで共有する運用にします。5〜10年分のバックフィルはBrave Search APIのカスタム日付範囲検索を使います。GitHub Secretsに `BRAVE_SEARCH_API_KEY` を設定したうえで、GitHub Actionsの `Build historical news archive` を手動実行してください。

レスポンス例:

```json
{
  "title": "SQMルーター国内マーケット最新情報",
  "generated_at": "2026-05-02T00:00:00Z",
  "count": 1,
  "items": [
    {
      "id": "6420ac1eec1812c4c79fd9ba",
      "title": "オンラインゲームやビデオ通話の遅延発生につながる「バッファブロート」が自分の使っているネットワークで発生するか否か測定できるウェブサイト...",
      "url": "https://news.google.com/rss/articles/...",
      "published_at": "2026-02-11T08:00:00Z",
      "source": "Google News JP: router market",
      "info_type": "国内ニュース検索",
      "score": 5
    }
  ]
}
```

通常利用では手動実行は不要です。GitHub Actionsが30分ごとにRSS/APIを更新します。

管理者として任意のタイミングで最新化したい場合だけ、GitHub Actionsの `Update router and network RSS feeds` を手動実行します。サーバーから叩くならGitHubのworkflow dispatch APIを使います。この操作にはGitHubトークンが必要です。

```bash
curl -X POST \
  -H "Authorization: Bearer <GITHUB_TOKEN>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/Psychidae/sqm-router-news-rss/actions/workflows/update-rss.yml/dispatches \
  -d '{"ref":"main"}'
```

その後、数十秒待ってから `api/latest.json` を取得します。

ログイン不要で使う場合は、手動発火せずに以下の公開URLをGETしてください。

```text
https://psychidae.github.io/sqm-router-news-rss/api/latest.json
```

## 設定

通常運用の設定は `config/feeds.json` に列挙されています。各ジャンルの検索条件は `config/*.json` に分けています。

- `config/feeds.json`: 生成するRSS/APIの一覧
- `config/japan-market.json`: SQM国内マーケット
- `config/router-domestic.json`: 国内ルーター市場
- `config/router-global.json`: 海外ルーターニュース
- `config/network-security.json`: ネットワークセキュリティ
- `config/network-performance.json`: ネットワーク性能・障害
- `config/archive.json`: 過去記事アーカイブ用の検索条件
- `app.lookback_hours`: 何時間以内の記事を対象にするか
- `app.max_items`: RSS/APIに載せる最大件数
- `filters.required_any`: どれかを含む記事・Issueだけ通すキーワード
- `filters.exclude_any`: 除外キーワード
- `sources`: RSS/Atomと検索APIの一覧
- `feed`: RSSのタイトル、説明、言語、公開URL
- `feed.feed_path`: GitHub Pages上のRSS公開パス
- `feed.api_path`: GitHub Pages上のJSON API公開パス
- `feed.item_date_mode`: `first_seen` の場合、Slack通知用にRSS itemの `pubDate` を初回発見日時にする
- `feed.preserve_existing_on_error`: 取得エラー時に空RSSで上書きしない保護

GitHub Actions上では `GITHUB_REPOSITORY` からGitHub Pages URLを自動推定します。独自ドメインや別URLを使う場合は、環境変数 `PAGES_SITE_URL` か `feed.site_url` を設定してください。

公開ページ:

- RSS/API一覧: [https://psychidae.github.io/sqm-router-news-rss/](https://psychidae.github.io/sqm-router-news-rss/)
- SQM RSS: [https://psychidae.github.io/sqm-router-news-rss/feed.xml](https://psychidae.github.io/sqm-router-news-rss/feed.xml)
- SQM JSON API: [https://psychidae.github.io/sqm-router-news-rss/api/latest.json](https://psychidae.github.io/sqm-router-news-rss/api/latest.json)
- 案内ページ: [https://psychidae.github.io/sqm-router-news-rss/](https://psychidae.github.io/sqm-router-news-rss/)

## 情報種別

RSS itemのタイトルには情報種別が入ります。

```text
[国内ニュース検索] オンラインゲームやビデオ通話の遅延発生につながる「バッファブロート」...
[国内全Web検索] OpenWrt カスタムルーターガイド...
[国内ルーター市場] Wi-Fi 7対応ルーター新製品...
[海外ルーターニュース] New mesh router review...
[セキュリティ] Router firmware vulnerability...
[性能・障害] DNS outage affects...
```

| 表示 | 意味 | 主なソース |
|---|---|---|
| 公式一次情報 | プロジェクト公式の更新・リリース・公式サイト | 広域設定のみ |
| 開発一次情報 | Issue/PRなど開発現場の一次情報 | 広域設定のみ |
| 国内ニュース検索 | 国内ニュース媒体や日本語記事の検索結果 | Google News JP |
| 国内全Webニュース検索 | Braveの国内ニュース検索結果 | Brave News JP |
| 国内全Web検索 | Braveの国内Web検索結果 | Brave Web JP |
| 国内ルーター市場 | 国内のルーター製品・レビュー・価格 | Google News JP, Brave JP |
| 海外ルーターニュース | 海外のルーター製品・レビュー・OpenWrt周辺 | Google News US, Brave Global |
| セキュリティ | ルーター・VPN・Firewall・Wi-Fiの脆弱性 | Google News, Brave |
| 性能・障害 | DNS、BGP、CDN、回線、遅延、通信障害 | Google News, Brave |
| 技術コミュニティ | 技術者コミュニティでの話題 | Hacker News（広域設定のみ） |
| 相談投稿 | ユーザーの困りごと・利用実態 | Reddit（広域設定のみ） |

## APIキーについて

必須のSlack APIキーはありません。

- `GITHUB_TOKEN`: GitHub Actionsでは自動で使われます。ローカルでは未設定でも可
- `BRAVE_SEARCH_API_KEY`: 任意。設定すると全Web検索/ニュース検索と過去5〜10年分のバックフィルが有効化されます
- `SLACK_WEBHOOK_URL`: RSS方式では不要。旧Webhook送信用機能を使う場合だけ必要

## 開発・検証

```bash
python3 -m unittest discover -s tests
python3 -m json.tool config/feeds.json >/dev/null
python3 src/generate_all_feeds.py --manifest config/feeds.json --index-output docs/index.html
python3 src/build_archive.py --config config/archive.json --years 10 --output-dir docs/archive
```
