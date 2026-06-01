# Deals Channel Workflow

This repository contains a lightweight semi-automated workflow for running an
affiliate deals channel:

1. Fetch affiliate feeds or APIs.
2. Remove weak deals with configurable filters.
3. Format shareable messages with hashtags.
4. Post approved deals to Telegram.
5. Save a WhatsApp-ready text file for manual sharing.

The workflow intentionally does not use a database. It deduplicates deals only
within each run.

## Quick start

```bash
cp config/deals.example.json config/deals.json
python3 scripts/deals_channel.py --config config/deals.json --dry-run
```

Dry runs skip Telegram posting but still write the WhatsApp output file.

## Configure feeds

Edit `config/deals.json` and replace the sample URLs. JSON feeds can map custom
field names from an affiliate API:

```json
{
  "name": "my-affiliate-api",
  "url": "https://partner.example.com/deals.json",
  "type": "json",
  "items_path": "deals",
  "title_field": "title",
  "url_field": "affiliate_url",
  "price_field": "price",
  "original_price_field": "mrp",
  "discount_percent_field": "discount_percent",
  "coupon_field": "coupon_code",
  "category_field": "category",
  "currency": "Rs. "
}
```

RSS and Atom feeds are also supported:

```json
{
  "name": "rss-deals",
  "url": "https://example.com/deals.rss",
  "type": "rss",
  "currency": "Rs. "
}
```

## Remove weak deals

Use the `filters` section to control what gets posted:

- `min_discount_percent`: minimum discount percentage.
- `min_savings_amount`: minimum absolute savings.
- `require_discount_data`: when `true`, drops deals without discount or price
  signals.
- `blocked_keywords`: drops deals containing these words.
- `required_keywords`: when set, keeps only deals containing at least one word.
- `max_items`: limits the number of deals per run.

When both discount percentage and savings filters are set, a deal can pass by
meeting either threshold.

## Telegram posting

Create a Telegram bot and set these environment variables before running without
`--dry-run`:

```bash
export TELEGRAM_BOT_TOKEN="123456:bot-token"
export TELEGRAM_CHAT_ID="@your_channel_or_chat_id"
python3 scripts/deals_channel.py --config config/deals.json
```

If Telegram credentials are missing, the script skips auto-posting and still
writes the WhatsApp file. Set `telegram.required` to `true` in the config if a
missing Telegram credential should fail the run.

## WhatsApp output

The script writes ready-to-copy messages to the configured output path, default:

```text
out/whatsapp_deals.txt
```

Override it for a single run:

```bash
python3 scripts/deals_channel.py --config config/deals.json --output out/today.txt --dry-run
```

## Automation

Run the script from cron, GitHub Actions, or any scheduler. A common safe setup
is:

```bash
python3 scripts/deals_channel.py --config config/deals.json --limit 5
```

Keep affiliate API keys in environment variables or scheduler secrets, and pass
them through feed headers only where your feed provider requires it.
