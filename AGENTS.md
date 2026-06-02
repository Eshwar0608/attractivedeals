# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

Single Python 3 CLI (`scripts/deals_channel.py`): fetch deals from feeds (Google Sheet CSV, JSON/RSS, or manual items in config), filter, optionally wrap URLs via Cuelinks, format messages, post to Telegram, and write WhatsApp-ready text. **No database, no web server, no Docker.** Stdlib only — no `pip install` step.

### Commands (from repo root)

| Task | Command |
|------|---------|
| Tests | `python3 -m unittest discover -s tests -v` |
| Local dry run (no secrets) | `python3 scripts/deals_channel.py --config config/simple-cuelinks.json --dry-run --skip-affiliate --limit 2` |
| Google Sheet dry run | Needs `GOOGLE_SHEET_CSV_URL`; add `--verbose` for feed diagnostics |

### Gotchas

- `main()` exits **1** when `fetched == 0` or `accepted == 0` (use `--allow-empty` to override).
- Google Sheet must be **published as CSV**; HTML login pages and header-only sheets raise explicit errors.
- CSV headers are normalized (BOM, case); required columns: `title`, `url`.
- `config/google-sheet-cuelinks.json` sets `telegram.required` and `affiliate.required` to **true** for production runs.
- Manual GitHub Actions runs default `dry_run: true` (no Telegram) unless changed in the workflow UI.

See `README.md` troubleshooting section for operator checklist.
