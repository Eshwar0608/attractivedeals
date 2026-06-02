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

## Troubleshooting (deals not reaching Telegram)

If GitHub Actions is green but nothing posts, open the latest **Run Deals Channel** log and check the JSON summary:

| Summary field | Meaning |
|---------------|---------|
| `skipped_feeds` contains `missing feed URL` | Set `GOOGLE_SHEET_CSV_URL` in GitHub Actions secrets |
| `errors` mentions HTML | Sheet is not **published as CSV** — use Publish to web, not only Share |
| `fetched: 0` with no errors | Sheet CSV is empty, wrong tab published, or column names do not include `title` and `url` |
| `fetched` > 0 but `accepted: 0` | Deals failed filters — add `discount_percent` or `price` + `original_price` |
| `telegram_posted: 0` on a scheduled run | Missing `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, or manual run left **`dry_run: true`** |

The script now **fails the workflow** when no deals are accepted or Telegram does not post (except `--dry-run` / `--allow-empty`), so silent empty runs should no longer show as success.

### Checklist

1. **Google Sheet row 1** (exact names, lowercase): `title`, `url`, `price`, `original_price`, `discount_percent`, `coupon`, `category`, `description`
2. **At least one data row** with non-empty `title` and `url`
3. **Publish to web** → CSV for the correct sheet tab; copy URL into secret `GOOGLE_SHEET_CSV_URL`
4. **GitHub secrets**: `GOOGLE_SHEET_CSV_URL`, `CUELINKS_CHANNEL_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
5. **Test Telegram**: Actions → Run Deals Channel → `dry_run: false`, `skip_affiliate: true`, `limit: 1`
6. **Production**: scheduled runs use `config/google-sheet-cuelinks.json` with Cuelinks enabled (`skip_affiliate: false`)

## Quick start

Best simple setup:

```bash
export GOOGLE_SHEET_CSV_URL="your-published-google-sheet-csv-url"
export CUELINKS_CHANNEL_ID="your-cuelinks-channel-id"
python3 scripts/deals_channel.py --config config/google-sheet-cuelinks.json --dry-run
```

Dry runs skip Telegram posting but still write the WhatsApp output file.


## Recommended setup: Google Sheet + Cuelinks + GitHub Actions

This is the simplest fully scheduled workflow without a database:

```text
Google Sheet CSV feed
        -> Python script on GitHub Actions
        -> Cuelinks affiliate URL wrapping
        -> Telegram auto-post
        -> WhatsApp text artifact
```

### 1. Create the Google Sheet

Create a sheet with these column headers in row 1:

```text
title,url,price,original_price,discount_percent,coupon,category,description
```

Example row:

```text
Boat headphones 45% off,https://www.flipkart.com/example,1099,1999,45,SAVE45,Electronics,Limited-time audio deal
```

Required columns are `title` and `url`. The filtering works best when either
`discount_percent` is set or both `price` and `original_price` are set.

### 2. Publish the sheet as CSV

In Google Sheets:

1. Open **File > Share > Publish to web**.
2. Select the sheet tab.
3. Choose **Comma-separated values (.csv)**.
4. Click **Publish**.
5. Copy the generated CSV URL.

Set it locally as:

```bash
export GOOGLE_SHEET_CSV_URL="https://docs.google.com/spreadsheets/d/.../pub?output=csv"
```

Or add it in GitHub as a repository secret named:

```text
GOOGLE_SHEET_CSV_URL
```

### 3. Add Cuelinks

Set your Cuelinks Channel ID:

```bash
export CUELINKS_CHANNEL_ID="your-cuelinks-channel-id"
```

The script converts each accepted deal URL into a Cuelinks redirect URL before
posting.

### 4. Run it

```bash
python3 scripts/deals_channel.py --config config/google-sheet-cuelinks.json --dry-run
```

GitHub Actions is already configured to use `config/google-sheet-cuelinks.json`
for scheduled runs. Add `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`GOOGLE_SHEET_CSV_URL`, and `CUELINKS_CHANNEL_ID` as GitHub Actions secrets,
then the scheduled run can fetch and post automatically.

## Simplest setup with Cuelinks

If you do not have private feed/API URLs yet, use the simple Cuelinks mode:

1. Create a Cuelinks account.
2. Get your Channel ID from **Cuelinks dashboard > Account > My Channels**.
3. Paste normal product URLs into `config/simple-cuelinks.json`.
4. Run the script with `CUELINKS_CHANNEL_ID`.

```bash
export CUELINKS_CHANNEL_ID="your-cuelinks-channel-id"
python3 scripts/deals_channel.py --config config/simple-cuelinks.json --dry-run
```

The script converts normal merchant URLs into Cuelinks redirect URLs like:

```text
https://linksredirect.com/?cid=YOUR_CHANNEL_ID&source=linkkit&url=...
```

This lets you start with one Cuelinks account instead of separate Amazon,
Flipkart, Ajio, Tata CLiQ, BigBasket, Instamart, Zepto, and Zomato feed/API
integrations. Later, when you get real feed/API URLs, switch back to
`config/deals.json`.

## Merchant feed URL environment variables

Affiliate feed/API URLs for these merchants are usually private to your approved
partner account or affiliate network. Set the feed URL variables you have access
to before running:

```bash
export AMAZON_IN_FEED_URL="https://your-approved-amazon-feed-or-proxy"
export FLIPKART_FEED_URL="https://your-approved-flipkart-feed"
export ZOMATO_FEED_URL="https://your-approved-zomato-feed"
export BIGBASKET_FEED_URL="https://your-approved-bigbasket-feed"
export INSTAMART_FEED_URL="https://your-approved-instamart-feed"
export ZEPTO_FEED_URL="https://your-approved-zepto-feed"
export AJIO_FEED_URL="https://your-approved-ajio-feed"
export TATACLIQ_FEED_URL="https://your-approved-tatacliq-feed"
```

Optional request headers can be set per merchant when your provider requires
them:

```bash
export FLIPKART_AUTH_HEADER="Bearer your-token"
export FLIPKART_API_KEY="your-api-key"
```

Use the same pattern for other merchants, for example
`AMAZON_IN_AUTH_HEADER`, `AJIO_API_KEY`, or `TATACLIQ_AUTH_HEADER`. If a feed
uses custom JSON field names, override those too, for example:

```bash
export AJIO_ITEMS_PATH="data.products"
export AJIO_URL_FIELD="deeplink"
export AJIO_ORIGINAL_PRICE_FIELD="mrp"
```

Amazon India's Product Advertising API is a signed POST API rather than a simple
feed URL. For this workflow, use an approved feed/export URL or a small PA-API
proxy that returns JSON or RSS.

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


## Test Telegram before adding Cuelinks

If you have `GOOGLE_SHEET_CSV_URL`, `TELEGRAM_BOT_TOKEN`, and
`TELEGRAM_CHAT_ID`, but you have not added `CUELINKS_CHANNEL_ID` yet, run a
manual Telegram delivery test by skipping affiliate wrapping.

From GitHub:

1. Open **Actions > Run Deals Channel > Run workflow**.
2. Set `dry_run` to `false`.
3. Set `skip_affiliate` to `true`.
4. Keep `config_path` as `config/google-sheet-cuelinks.json`.
5. Set `limit` to `1`.
6. Run the workflow.

This posts one deal to Telegram using the original URL from the Google Sheet.
After Telegram posting is confirmed, add `CUELINKS_CHANNEL_ID` and run again
with `skip_affiliate` set to `false` so links are monetized.

Local equivalent:

```bash
GOOGLE_SHEET_CSV_URL="your-published-csv-url" \
TELEGRAM_BOT_TOKEN="your-bot-token" \
TELEGRAM_CHAT_ID="@your_channel" \
python3 scripts/deals_channel.py \
  --config config/google-sheet-cuelinks.json \
  --limit 1 \
  --skip-affiliate
```

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


## Run automatically with GitHub Actions

The repository includes `.github/workflows/run-deals.yml`, which runs the deals
workflow in two ways:

- Manual run from the GitHub **Actions** tab with `workflow_dispatch`. Manual
  runs default to dry-run, so they create the WhatsApp file without posting to
  Telegram unless you turn dry-run off. The default config is
  `config/google-sheet-cuelinks.json`. Use `skip_affiliate=true` when testing
  Telegram before adding `CUELINKS_CHANNEL_ID`.
- Scheduled run every 6 hours using GitHub cron. Scheduled runs are real runs:
  they fetch the Google Sheet CSV, wrap links with Cuelinks, and post to
  Telegram when the required secrets are configured.

Add your secrets in GitHub under **Settings > Secrets and variables > Actions**:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
GOOGLE_SHEET_CSV_URL
CUELINKS_CHANNEL_ID
AMAZON_IN_FEED_URL
FLIPKART_FEED_URL
ZOMATO_FEED_URL
BIGBASKET_FEED_URL
INSTAMART_FEED_URL
ZEPTO_FEED_URL
AJIO_FEED_URL
TATACLIQ_FEED_URL
```

For the recommended Google Sheet setup, `GOOGLE_SHEET_CSV_URL`,
`CUELINKS_CHANNEL_ID`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` are enough.
For merchant feed/API setup, only the feed URL secrets you actually use are
required. Optional auth secrets like `FLIPKART_AUTH_HEADER` or `AJIO_API_KEY`
can be added if your affiliate provider requires request headers. After each
run, GitHub uploads
`out/whatsapp_deals.txt` as a workflow artifact so you can download the
WhatsApp-ready copy.

To change the schedule, edit the cron value in `.github/workflows/run-deals.yml`.
GitHub cron times are in UTC.

## Automation

Run the script from cron, GitHub Actions, or any scheduler. A common safe setup
is:

```bash
python3 scripts/deals_channel.py --config config/deals.json --limit 5
```

Keep affiliate API keys in environment variables or scheduler secrets, and pass
them through feed headers only where your feed provider requires it.
