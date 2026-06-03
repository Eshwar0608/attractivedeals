# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

Single Python 3 CLI (`scripts/deals_channel.py`): **Cuelinks Offers API** (`type: cuelinks_offers`), optional **Google Sheet CSV** (`type: csv`), brand allowlist, dedupe via `out/posted_deals.json`, Cuelinks URL wrap, Telegram posts. Writes `out/deals.csv` and `out/messages.txt`. Stdlib only — no database, Docker, or web server.

**Production config:** `config/brands-only-telegram.json` (GitHub Actions cron every 15 minutes UTC).

### Commands (repo root)

| Task | Command |
|------|---------|
| Tests | `python3 -m unittest discover -s tests -v` |
| Dry run | `python3 scripts/deals_channel.py --config config/brands-only-telegram.json --dry-run --verbose` |
| Local manual feed test | `python3 scripts/deals_channel.py --config <tmp-config-with-manual-feed> --dry-run --skip-affiliate --limit 2` |

### Gotchas

- `main()` exits **1** when `fetched == 0` or `accepted == 0` unless `--allow-empty`.
- Production config sets `telegram.required` and `affiliate.required` to **true**.
- Manual workflow_dispatch defaults `dry_run: true` (no Telegram).
- Dedupe state is cached in Actions as `deals-posted-<repo>`; “Cache not found” on first run is expected.
- Google Sheet must be **published as CSV**; HTML responses raise a clear error.
- Config accepts `messages_output` or legacy `whatsapp` key with `output_file` only (no Cloud API).

See `README.md` for operator secrets and troubleshooting.
