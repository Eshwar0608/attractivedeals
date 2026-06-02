# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

Single Python 3 CLI (`scripts/deals_channel.py`): fetch deals from feeds (Google Sheet CSV, JSON/RSS, or manual items in config), filter, optionally wrap URLs via Cuelinks, format messages, post to Telegram, and write WhatsApp-ready text. **No database, no web server, no Docker.** Stdlib only — no `pip install` step.

### Services

There are no long-lived local services. E2E is a one-shot script run against external HTTP APIs when configured.

| Dependency | When needed |
|------------|-------------|
| Python 3.12+ on PATH | Always |
| Feed URL or manual items in config | Always (manual mode needs no network) |
| `CUELINKS_CHANNEL_ID` | When `affiliate.required` is true and not using `--skip-affiliate` |
| `GOOGLE_SHEET_CSV_URL` | For `config/google-sheet-cuelinks.json` |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Live Telegram posts (skipped with `--dry-run`) |

### Commands (from repo root)

See `README.md` for full setup. Quick reference:

| Task | Command |
|------|---------|
| Tests | `python3 -m unittest discover -s tests -v` |
| Syntax check | `python3 -m py_compile scripts/deals_channel.py` |
| Local dry run (no secrets) | `python3 scripts/deals_channel.py --config config/simple-cuelinks.json --dry-run --skip-affiliate --limit 2` |
| Build | N/A |

Lint/format tools (ruff, black, mypy) are **not** configured in this repo.

### Gotchas

- `config/simple-cuelinks.json` sets `affiliate.required: true`; without `CUELINKS_CHANNEL_ID`, runs fail unless you pass `--skip-affiliate`.
- `--dry-run` skips Telegram but still writes the WhatsApp output file.
- Scheduled/production behavior is defined in `.github/workflows/run-deals.yml` (cron + `workflow_dispatch`).
