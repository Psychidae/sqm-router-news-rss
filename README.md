# SQMルーター国内マーケットRSS

SQMルーター、OpenWrt SQM、Bufferbloat、CAKE、`luci-app-sqm` などの最新情報を集め、SlackのRSS appで購読できる `feed.xml` を生成するシステムです。

Slack API、Incoming Webhook、Slack Appの作成は不要です。Slack側では公開RSS URLをRSS appに登録するだけです。

## できること

- 国内向け設定ではGoogle News JP、Bing News JP、Brave Search JP、公式更新、GitHub開発一次情報から取得
- SQM関連キーワードでフィルタリング
- RSS itemのタイトルに `[開発一次情報]` などの情報種別を表示
- GitHub Actionsで `docs/feed.xml` を毎日更新
- GitHub PagesでRSSを公開し、Slack RSS appに登録
- 広域調査用に `config/sqm-router.json` も残す

## 最短セットアップ

1. GitHubにこのフォルダをpushする
2. GitHub Pagesを有効化する
   - `Settings > Pages`
   - `Deploy from a branch`
   - Branch: `main`
   - Folder: `/docs`
3. GitHub Actionsを有効化する
4. 任意で `Settings > Secrets and variables > Actions` に `BRAVE_SEARCH_API_KEY` を登録する
5. SlackのRSS appに次のURLを登録する

```text
https://psychidae.github.io/sqm-router-news-rss/feed.xml
```

Slack公式手順: [Add RSS feeds to Slack](https://slack.com/hc/en-us/articles/218688467-Add-RSS-feeds-to-Slack)

## ローカルでRSSを生成

```bash
python3 src/news_to_slack.py \
  --config config/japan-market.json \
  --rss-output docs/feed.xml \
  --index-output docs/index.html
```

生成後、ブラウザやRSSリーダーで `docs/feed.xml` を確認できます。

## 設定

通常運用の設定は `config/japan-market.json` です。海外コミュニティやRedditまで広く見る場合は `config/sqm-router.json` を使います。

- `app.lookback_hours`: 何時間以内の記事を対象にするか
- `app.max_items`: RSSに載せる最大件数
- `filters.required_any`: どれかを含む記事・Issueだけ通すキーワード
- `filters.exclude_any`: 除外キーワード
- `sources`: RSS/Atomと検索APIの一覧
- `feed`: RSSのタイトル、説明、言語、公開URL
- `feed.preserve_existing_on_error`: 取得エラー時に空RSSで上書きしない保護

GitHub Actions上では `GITHUB_REPOSITORY` からGitHub Pages URLを自動推定します。独自ドメインや別URLを使う場合は、環境変数 `PAGES_SITE_URL` か `feed.site_url` を設定してください。

公開ページ:

- RSS: [https://psychidae.github.io/sqm-router-news-rss/feed.xml](https://psychidae.github.io/sqm-router-news-rss/feed.xml)
- 案内ページ: [https://psychidae.github.io/sqm-router-news-rss/](https://psychidae.github.io/sqm-router-news-rss/)

## 情報種別

RSS itemのタイトルには情報種別が入ります。

```text
[開発一次情報] SQM CAKE MQ cake_mq: Speed / Bandwidth is very low...
[国内ニュース検索] Google News JP: SQM domestic market...
```

| 表示 | 意味 | 主なソース |
|---|---|---|
| 公式一次情報 | プロジェクト公式の更新・リリース・公式サイト | OpenWrt releases, OpenWrt site |
| 開発一次情報 | Issue/PRなど開発現場の一次情報 | GitHub Search |
| 国内ニュース検索 | 国内ニュース媒体や日本語記事の検索結果 | Google News JP, Bing News JP |
| 国内全Webニュース検索 | Braveの国内ニュース検索結果 | Brave News JP |
| 国内全Web検索 | Braveの国内Web検索結果 | Brave Web JP |
| 技術コミュニティ | 技術者コミュニティでの話題 | Hacker News（広域設定のみ） |
| 相談投稿 | ユーザーの困りごと・利用実態 | Reddit（広域設定のみ） |

## APIキーについて

必須のSlack APIキーはありません。

- `GITHUB_TOKEN`: GitHub Actionsでは自動で使われます。ローカルでは未設定でも可
- `BRAVE_SEARCH_API_KEY`: 任意。設定すると国内全Web検索/ニュース検索が有効化されます
- `SLACK_WEBHOOK_URL`: RSS方式では不要。旧Webhook送信用機能を使う場合だけ必要

## 開発・検証

```bash
python3 -m unittest discover -s tests
python3 -m json.tool config/japan-market.json >/dev/null
python3 src/news_to_slack.py --config config/japan-market.json --rss-output docs/feed.xml --index-output docs/index.html
```
