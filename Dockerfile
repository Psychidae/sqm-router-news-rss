FROM python:3.12-slim

WORKDIR /app
COPY . .

CMD ["python", "src/news_to_slack.py", "--config", "config/japan-market.json", "--rss-output", "docs/feed.xml", "--index-output", "docs/index.html"]
