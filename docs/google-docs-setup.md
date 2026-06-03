# Google Docs output setup

The deals workflow can append each run’s **Telegram messages** and **deals CSV** to a Google Doc (same content as `out/messages.txt` and `out/deals.csv`).

Default document (config): `1LZJGJwvoK3UskdjKxoQ_VzSk3eBPmusX6eX4YSxM2qA`  
https://docs.google.com/document/d/1LZJGJwvoK3UskdjKxoQ_VzSk3eBPmusX6eX4YSxM2qA/edit

## 1. Google Cloud service account

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **Google Docs API**.
3. Create a **Service account** and download the JSON key.
4. Copy the service account email (e.g. `deals-bot@project.iam.gserviceaccount.com`).
5. Open your Google Doc → **Share** → add that email as **Editor**.

## 2. Credentials in this repo / CI

```bash
pip install -r requirements.txt
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

For GitHub Actions, store the full JSON in secret `GOOGLE_SERVICE_ACCOUNT_JSON` and write it to a file before the run (see workflow).

Optional: short-lived token instead of a key file:

```bash
export GOOGLE_DOCS_ACCESS_TOKEN="ya29...."
```

## 3. Disable or skip

- Set `"google_docs": { "enabled": false }` in config, or
- Run with `--skip-google-docs`.

## Merchant allowlist

- **Empty** `allowed_merchants` in `config/allowed-merchants.json` → all deals are eligible (other filters still apply).
- **Non-empty** list → only those brands (domain / merchant match) are posted.
