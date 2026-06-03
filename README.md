# Attractive Deals (Telegram)

Brand-only affiliate deals pipeline: **Cuelinks Offers API** → filter famous stores → dedupe → Cuelinks link wrap → **Telegram** (promo-style messages). Optional Google Sheet CSV feed. No database.

**Production config:** `config/brands-only-telegram.json` — runs every **15 minutes** on GitHub Actions.

## Architecture

```text
Cuelinks Offers API ──┐
Google Sheet CSV      ──┼→ brand filter → dedupe → out/deals.csv → Telegram
(manual test items)   ──┘                      → out/messages.txt (copy/paste)
```

## Quick start

```bash
export CUELINKS_API_TOKEN="your-api-token"
export CUELINKS_CHANNEL_ID="your-channel-id"
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="@your_channel"

python3 scripts/deals_channel.py --config config/brands-only-telegram.json --dry-run --verbose
```

Remove `--dry-run` to post to Telegram.

### Optional Google Sheet

1. Copy headers from `config/google-sheet-template.csv`.
2. Publish the sheet as CSV and set `GOOGLE_SHEET_CSV_URL`.
3. Enable the `google-sheet-curated` feed in `config/brands-only-telegram.json` (`"enabled": true`).

Allowed stores are listed in `config/allowed-merchants.json` (Amazon, Flipkart, Myntra, etc.). **Leave the list empty to allow all merchants**; add names to filter to only those brands.

### Google Doc output

Each run can append the same content as `out/messages.txt` and `out/deals.csv` to your Google Doc. See [docs/google-docs-setup.md](docs/google-docs-setup.md). Production config targets:

https://docs.google.com/document/d/1LZJGJwvoK3UskdjKxoQ_VzSk3eBPmusX6eX4YSxM2qA/edit

```bash
pip install -r requirements.txt
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
python3 scripts/deals_channel.py --config config/brands-only-telegram.json --dry-run --verbose
```

Use `--skip-google-docs` to disable Doc append for one run.

## GitHub Actions secrets

| Secret | Required |
|--------|----------|
| `CUELINKS_API_TOKEN` | Yes |
| `CUELINKS_CHANNEL_ID` | Yes |
| `TELEGRAM_BOT_TOKEN` | Yes |
| `TELEGRAM_CHAT_ID` | Yes |
| `GOOGLE_SHEET_CSV_URL` | Only if sheet feed is enabled |
| `CUELINKS_OFFERS_URL`, `CUELINKS_OFFERS_ITEMS_PATH`, `CUELINKS_OFFERS_CATEGORY` | Optional API overrides |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service account JSON path for Google Docs append |
| `GOOGLE_DOCS_ACCESS_TOKEN` | Optional bearer token instead of service account |
| `GOOGLE_DOCS_DOCUMENT_ID` | Override Doc ID (default is in config) |

Manual run: **Actions → Run Deals Channel** — set `dry_run: false` to post.

## Troubleshooting

| Log / summary | What to do |
|---------------|------------|
| `brand-only filter: N allowed store(s)` | Merchant allowlist is active |
| `merchant filter: rejected N` | Deals are from non-allowed stores |
| `cross-run dedupe: skipped N` | Normal — already posted campaigns |
| `Cache not found` on first run | Normal — dedupe cache starts empty |
| `fetched: 0` | Check `CUELINKS_API_TOKEN` and API access |
| `accepted: 0`, all deduped | Wait for new offers or `--reset-posted` (reposts everything) |

Clear dedupe history (use carefully):

```bash
python3 scripts/deals_channel.py --config config/brands-only-telegram.json --reset-posted --dry-run
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Promo message format

Set `message_format.style` to `promo` in config. Sheet columns `links`, `coupon`, `bank_offer`, and optional `message` (full override) control the post body.
