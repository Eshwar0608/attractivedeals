# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

Single Python 3 CLI (`scripts/deals_channel.py`): **auto-fetch** deals from **Cuelinks Offers API** (`type: cuelinks_offers`), optionally merge Google Sheet CSV, filter, write **`out/deals.csv`**, wrap URLs via Cuelinks, post to Telegram, and write WhatsApp-ready text. **No database, no web server, no Docker.** Stdlib only.

**Default config:** `config/auto-fetch-telegram.json` (GitHub Actions scheduled runs).

### Commands (from repo root)

| Task | Command |
|------|---------|
| Tests | `python3 -m unittest discover -s tests -v` |
| Auto-fetch dry run | Needs `CUELINKS_API_TOKEN`, `CUELINKS_CHANNEL_ID`; `python3 scripts/deals_channel.py --config config/auto-fetch-telegram.json --dry-run --verbose` |
| Local dry run (no secrets) | `python3 scripts/deals_channel.py --config config/simple-cuelinks.json --dry-run --skip-affiliate --limit 2` |

### Gotchas

- `main()` exits **1** when `fetched == 0` or `accepted == 0` (use `--allow-empty` to override).
- Google Sheet must be **published as CSV**; HTML login pages and header-only sheets raise explicit errors.
- CSV headers are normalized (BOM, case); required columns: `title`, `url`.
- Auto-fetch requires **`CUELINKS_API_TOKEN`** (Offers API; request from sales@cuelinks.com).
- `config/auto-fetch-telegram.json` sets `telegram.required` and `affiliate.required` to **true**.
- Manual GitHub Actions runs default `dry_run: true` (no Telegram) unless changed in the workflow UI.

See `README.md` troubleshooting section for operator checklist.
