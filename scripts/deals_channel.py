#!/usr/bin/env python3
"""Semi-automated affiliate deals workflow.

Fetches affiliate feeds, filters weak deals, formats shareable messages, posts
to Telegram, and writes WhatsApp-ready copy without using a database.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_USER_AGENT = "deals-channel-workflow/1.0"
ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")
DEFAULT_CUELINKS_OFFERS_URLS = (
    "https://www.cuelinks.com/api/v2/offers.json",
    "https://www.cuelinks.com/api/v1/offers.json",
)
CUELINKS_ITEM_PATHS = ("offers", "data.offers", "data", "results", "items", "coupons", "deals")


@dataclass
class FeedConfig:
    name: str
    url: str = ""
    enabled: bool = True
    type: str = "auto"
    headers: dict[str, str] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    items_path: str | None = None
    title_field: str = "title"
    url_field: str = "url"
    price_field: str = "price"
    original_price_field: str = "original_price"
    discount_percent_field: str = "discount_percent"
    coupon_field: str = "coupon"
    category_field: str = "category"
    description_field: str = "description"
    currency: str = ""
    api_token_env: str = "CUELINKS_API_TOKEN"
    max_pages: int = 5
    per_page: int = 50
    category: str = ""


@dataclass
class ExportCsvConfig:
    enabled: bool = True
    output_file: str = "out/deals.csv"


@dataclass
class FilterConfig:
    min_discount_percent: float = 25.0
    min_savings_amount: float = 0.0
    require_discount_data: bool = False
    blocked_keywords: list[str] = field(default_factory=list)
    required_keywords: list[str] = field(default_factory=list)
    max_items: int = 10


@dataclass
class TelegramConfig:
    enabled: bool = True
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    disable_web_page_preview: bool = False
    timeout_seconds: int = 15
    required: bool = False


@dataclass
class WhatsAppConfig:
    output_file: str = "out/whatsapp_deals.txt"


@dataclass
class AffiliateConfig:
    enabled: bool = False
    network: str = "none"
    channel_id: str = ""
    channel_id_env: str = "CUELINKS_CHANNEL_ID"
    source: str = "linkkit"
    required: bool = False


@dataclass
class WorkflowConfig:
    feeds: list[FeedConfig]
    filters: FilterConfig = field(default_factory=FilterConfig)
    hashtags: list[str] = field(default_factory=lambda: ["#deals"])
    affiliate: AffiliateConfig = field(default_factory=AffiliateConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    export_csv: ExportCsvConfig = field(default_factory=ExportCsvConfig)


@dataclass
class Deal:
    source: str
    title: str
    url: str
    price: float | None = None
    original_price: float | None = None
    discount_percent: float | None = None
    coupon: str | None = None
    category: str | None = None
    description: str | None = None
    currency: str = ""

    @property
    def savings_amount(self) -> float | None:
        if self.price is None or self.original_price is None:
            return None
        savings = self.original_price - self.price
        return savings if savings > 0 else None


@dataclass
class RunSummary:
    fetched: int = 0
    accepted: int = 0
    skipped: int = 0
    telegram_posted: int = 0
    telegram_failed: int = 0
    whatsapp_file: str | None = None
    csv_file: str | None = None
    skipped_feeds: list[str] = field(default_factory=list)
    feed_details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def config_fields(cls: type) -> set[str]:
    return {item.name for item in dataclasses.fields(cls)}


def load_config(path: Path) -> WorkflowConfig:
    with path.open("r", encoding="utf-8") as config_file:
        raw = expand_config_env(json.load(config_file))

    feed_fields = config_fields(FeedConfig)
    feeds = [FeedConfig(**{k: v for k, v in feed.items() if k in feed_fields}) for feed in raw.get("feeds", [])]
    if not feeds:
        raise ValueError("Config must include at least one feed.")

    export_raw = raw.get("export_csv", {})
    export_fields = config_fields(ExportCsvConfig)

    return WorkflowConfig(
        feeds=feeds,
        filters=FilterConfig(**raw.get("filters", {})),
        hashtags=raw.get("hashtags", ["#deals"]),
        affiliate=AffiliateConfig(**raw.get("affiliate", {})),
        telegram=TelegramConfig(**raw.get("telegram", {})),
        whatsapp=WhatsAppConfig(**raw.get("whatsapp", {})),
        export_csv=ExportCsvConfig(**{k: v for k, v in export_raw.items() if k in export_fields}),
    )


def expand_config_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: expand_config_env(child) for key, child in value.items()}
    if isinstance(value, list):
        return [expand_config_env(child) for child in value]
    if isinstance(value, str):
        return expand_env_placeholders(value)
    return value


def expand_env_placeholders(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        default = match.group(2)
        if env_name in os.environ:
            return os.environ[env_name]
        return default if default is not None else ""

    return ENV_PLACEHOLDER_RE.sub(replace, value)


def fetch_text(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    if not url:
        raise ValueError("Feed URL is empty. Set the merchant feed URL in config/deals.json or env vars.")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("", "file"):
        path = Path(urllib.parse.unquote(parsed.path if parsed.scheme else url))
        return path.read_text(encoding="utf-8")

    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    request_headers.update({key: value for key, value in (headers or {}).items() if value})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


def cuelinks_auth_header(token: str) -> dict[str, str]:
    return {
        "Authorization": f'Token token="{token}"',
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }


def normalize_item_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip().lower().replace(" ", "_"): value for key, value in item.items() if key is not None}


def discover_cuelinks_items(payload: Any, items_path: str | None = None) -> list[Any]:
    if items_path:
        items = extract_items(payload, items_path)
        if items:
            return items
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for path in CUELINKS_ITEM_PATHS:
            items = extract_items(payload, path)
            if items:
                return items
    return []


def fetch_cuelinks_offers(feed: FeedConfig) -> list[Deal]:
    token = os.environ.get(feed.api_token_env, "").strip()
    if not token:
        raise ValueError(
            f"Set {feed.api_token_env} to fetch live offers from Cuelinks. "
            "Request API access from sales@cuelinks.com (publisher account required)."
        )

    candidate_urls: list[str] = []
    if feed.url:
        candidate_urls.append(feed.url)
    for default_url in DEFAULT_CUELINKS_OFFERS_URLS:
        if default_url not in candidate_urls:
            candidate_urls.append(default_url)

    headers = cuelinks_auth_header(token)
    headers.update({key: value for key, value in feed.headers.items() if value})
    last_error: Exception | None = None
    deals: list[Deal] = []

    for base_url in candidate_urls:
        try:
            deals = fetch_cuelinks_offers_from_url(feed, base_url, headers)
            if deals:
                return deals
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc

    if last_error:
        raise ValueError(
            f"Could not fetch Cuelinks offers from configured URLs. Last error: {last_error}. "
            "Set CUELINKS_OFFERS_URL to the Offers API URL from your Cuelinks API documentation."
        ) from last_error
    raise ValueError("Cuelinks offers API returned no deals.")


def fetch_cuelinks_offers_from_url(
    feed: FeedConfig,
    base_url: str,
    headers: dict[str, str],
) -> list[Deal]:
    deals: list[Deal] = []
    remote = urllib.parse.urlparse(base_url).scheme in ("http", "https")
    max_pages = max(feed.max_pages, 1) if remote else 1
    for page in range(1, max_pages + 1):
        page_url = base_url
        if remote:
            query: dict[str, str] = {
                "page": str(page),
                "per_page": str(feed.per_page),
            }
            if feed.category:
                query["category"] = feed.category
            separator = "&" if "?" in base_url else "?"
            page_url = f"{base_url}{separator}{urllib.parse.urlencode(query)}"

        body = fetch_text(page_url, headers=headers)
        payload = json.loads(body)
        items = discover_cuelinks_items(payload, feed.items_path)
        normalized_items = [
            normalize_item_keys(item) if isinstance(item, dict) else item for item in items
        ]
        page_deals = parse_json_items(feed, normalized_items)
        deals.extend(page_deals)
        if not page_deals or len(items) < feed.per_page:
            break

    return deals


def parse_feed(feed: FeedConfig) -> list[Deal]:
    feed_type = feed.type.lower()
    if feed_type in ("manual", "inline"):
        return parse_json_items(feed, feed.items)
    if feed_type == "cuelinks_offers":
        return fetch_cuelinks_offers(feed)

    body = fetch_text(feed.url, feed.headers)
    if feed_type == "auto":
        stripped = body.lstrip()
        feed_type = "json" if stripped.startswith(("{", "[")) else "rss"

    if feed_type == "json":
        return parse_json_feed(feed, body)
    if feed_type == "csv":
        return parse_csv_feed(feed, body)
    if feed_type in ("rss", "atom", "xml"):
        return parse_xml_feed(feed, body)
    raise ValueError(f"Unsupported feed type for {feed.name}: {feed.type}")


def parse_json_feed(feed: FeedConfig, body: str) -> list[Deal]:
    payload = json.loads(body)
    return parse_json_items(feed, extract_items(payload, feed.items_path))


def parse_json_items(feed: FeedConfig, items: list[Any]) -> list[Deal]:
    deals: list[Deal] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        title = first_text(
            get_nested(item, feed.title_field),
            get_nested(item, "name"),
            get_nested(item, "product_name"),
            get_nested(item, "offer_title"),
            get_nested(item, "offer_name"),
            get_nested(item, "campaign_name"),
        )
        url = first_text(
            get_nested(item, feed.url_field),
            get_nested(item, "link"),
            get_nested(item, "deeplink"),
            get_nested(item, "affiliate_url"),
            get_nested(item, "offer_url"),
            get_nested(item, "landing_url"),
            get_nested(item, "merchant_url"),
            get_nested(item, "tracking_url"),
        )
        if not title or not url:
            continue

        price = parse_money(get_nested(item, feed.price_field))
        original_price = parse_money(get_nested(item, feed.original_price_field))
        discount_percent = parse_percent(
            get_nested(item, feed.discount_percent_field),
            title,
            original_price,
            price,
        )

        deals.append(
            Deal(
                source=feed.name,
                title=clean_text(title),
                url=clean_text(url),
                price=price,
                original_price=original_price,
                discount_percent=discount_percent,
                coupon=clean_optional_text(
                    first_text(
                        get_nested(item, feed.coupon_field),
                        get_nested(item, "coupon_code"),
                        get_nested(item, "promo_code"),
                    )
                ),
                category=clean_optional_text(get_nested(item, feed.category_field)),
                description=clean_optional_text(get_nested(item, feed.description_field)),
                currency=feed.currency,
            )
        )

    return deals



def looks_like_html_feed(body: str) -> bool:
    sample = body.lstrip()[:800].lower()
    return sample.startswith("<") or "<!doctype html" in sample or "<html" in sample


def normalize_sheet_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize Google Sheet / CSV column names (BOM, case, common aliases)."""
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if key is None:
            continue
        clean_key = str(key).strip().lstrip("\ufeff").lower().replace(" ", "_")
        if clean_key:
            normalized[clean_key] = value
    if "link" in normalized and "url" not in normalized:
        normalized["url"] = normalized["link"]
    if "product_url" in normalized and "url" not in normalized:
        normalized["url"] = normalized["product_url"]
    return normalized


def parse_csv_feed(feed: FeedConfig, body: str) -> list[Deal]:
    if looks_like_html_feed(body):
        raise ValueError(
            "Feed response looks like HTML, not CSV. Publish the Google Sheet as CSV "
            "(File > Share > Publish to web > Comma-separated values) and set "
            "GOOGLE_SHEET_CSV_URL to that published URL."
        )

    text = body.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames:
        reader.fieldnames = [
            (name or "").strip().lstrip("\ufeff") for name in reader.fieldnames
        ]
    rows = [normalize_sheet_row(row) for row in reader]
    if not rows:
        raise ValueError(
            "CSV feed has no data rows. Add at least one deal row below the header "
            "(required columns: title, url)."
        )
    deals = parse_json_items(feed, rows)
    if not deals:
        headers = ", ".join(reader.fieldnames or [])
        raise ValueError(
            "CSV has data rows but none produced deals. Required columns: title, url "
            f"(detected headers: {headers or 'none'})."
        )
    return deals

def parse_xml_feed(feed: FeedConfig, body: str) -> list[Deal]:
    root = ET.fromstring(body)
    elements = root.findall(".//item") or root.findall(".//{*}entry")
    deals: list[Deal] = []

    for element in elements:
        title = child_text(element, "title")
        url = child_text(element, "link")
        if not url:
            link = element.find("{*}link")
            url = link.attrib.get("href", "") if link is not None else ""
        description = first_text(
            child_text(element, "description"),
            child_text(element, "summary"),
            child_text(element, "content"),
        )
        if not title or not url:
            continue
        discount_percent = parse_percent(None, title, None, None)
        deals.append(
            Deal(
                source=feed.name,
                title=clean_text(title),
                url=clean_text(url),
                discount_percent=discount_percent,
                category=clean_optional_text(child_text(element, "category")),
                description=clean_optional_text(description),
                currency=feed.currency,
            )
        )

    return deals


def extract_items(payload: Any, items_path: str | None) -> list[Any]:
    if items_path:
        payload = get_nested(payload, items_path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "deals", "products", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def get_nested(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = dict_lookup(current, part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def dict_lookup(data: dict[str, Any], key: str) -> Any:
    if key in data:
        return data[key]
    lowered = key.lower()
    for candidate, value in data.items():
        if str(candidate).lower() == lowered:
            return value
    return None


def child_text(element: ET.Element, child_name: str) -> str | None:
    for child in element:
        if strip_namespace(child.tag) == child_name:
            return child.text
    return None


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def filter_deals(deals: list[Deal], filters: FilterConfig) -> list[Deal]:
    accepted: list[Deal] = []
    seen: set[str] = set()

    for deal in deals:
        key = deal_key(deal)
        if key in seen:
            continue
        seen.add(key)

        if not is_allowed_by_keywords(deal, filters):
            continue
        if not is_strong_enough(deal, filters):
            continue
        accepted.append(deal)
        if filters.max_items and len(accepted) >= filters.max_items:
            break

    return accepted


def deal_key(deal: Deal) -> str:
    parsed = urllib.parse.urlparse(deal.url)
    normalized_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", "")
    )
    return normalized_url or deal.title.lower()


def is_allowed_by_keywords(deal: Deal, filters: FilterConfig) -> bool:
    searchable = " ".join(
        value
        for value in [deal.title, deal.description or "", deal.category or ""]
        if value
    ).lower()
    if any(keyword.lower() in searchable for keyword in filters.blocked_keywords):
        return False
    if filters.required_keywords:
        return any(keyword.lower() in searchable for keyword in filters.required_keywords)
    return True


def is_strong_enough(deal: Deal, filters: FilterConfig) -> bool:
    discount = deal.discount_percent
    savings = deal.savings_amount
    has_discount_signal = discount is not None or savings is not None

    discount_ok = (
        filters.min_discount_percent <= 0
        or (discount is not None and discount >= filters.min_discount_percent)
    )
    savings_ok = (
        filters.min_savings_amount <= 0
        or (savings is not None and savings >= filters.min_savings_amount)
    )

    if filters.min_discount_percent <= 0 and filters.min_savings_amount <= 0:
        return True
    if discount_ok or savings_ok:
        return True
    return not filters.require_discount_data and not has_discount_signal


def format_deal(deal: Deal, hashtags: list[str]) -> str:
    lines = [f"🔥 {deal.title}"]

    price_line = build_price_line(deal)
    if price_line:
        lines.append(price_line)

    if deal.discount_percent is not None:
        lines.append(f"Discount: {format_number(deal.discount_percent)}% off")
    elif deal.savings_amount is not None:
        lines.append(f"Save: {deal.currency}{format_number(deal.savings_amount)}")

    if deal.coupon:
        lines.append(f"Coupon: {deal.coupon}")

    summary = summarize(deal.description)
    if summary:
        lines.append(summary)

    lines.append(deal.url)

    tags = build_hashtags(hashtags, deal.category)
    if tags:
        lines.append(" ".join(tags))

    return "\n".join(lines)


def build_price_line(deal: Deal) -> str | None:
    if deal.price is None and deal.original_price is None:
        return None
    if deal.price is not None and deal.original_price is not None:
        return (
            f"Price: {deal.currency}{format_number(deal.price)} "
            f"(was {deal.currency}{format_number(deal.original_price)})"
        )
    if deal.price is not None:
        return f"Price: {deal.currency}{format_number(deal.price)}"
    return f"Original price: {deal.currency}{format_number(deal.original_price)}"


def build_hashtags(base_hashtags: list[str], category: str | None) -> list[str]:
    tags = [normalize_hashtag(tag) for tag in base_hashtags]
    if category:
        tags.append(normalize_hashtag(category))
    deduped: list[str] = []
    for tag in tags:
        if tag and tag not in deduped:
            deduped.append(tag)
    return deduped


def normalize_hashtag(value: str) -> str:
    tag = re.sub(r"[^A-Za-z0-9_]+", "", value.strip().replace("#", ""))
    return f"#{tag.lower()}" if tag else ""


def save_whatsapp_messages(messages: list[str], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n\n---\n\n".join(messages) + ("\n" if messages else ""), encoding="utf-8")


def save_deals_csv(deals: list[Deal], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source",
        "title",
        "url",
        "price",
        "original_price",
        "discount_percent",
        "coupon",
        "category",
        "description",
    ]
    with output_file.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for deal in deals:
            writer.writerow(
                {
                    "source": deal.source,
                    "title": deal.title,
                    "url": deal.url,
                    "price": deal.price if deal.price is not None else "",
                    "original_price": deal.original_price if deal.original_price is not None else "",
                    "discount_percent": deal.discount_percent if deal.discount_percent is not None else "",
                    "coupon": deal.coupon or "",
                    "category": deal.category or "",
                    "description": deal.description or "",
                }
            )


def post_messages_to_telegram(
    messages: list[str],
    telegram: TelegramConfig,
    dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    if dry_run or not telegram.enabled:
        return 0, 0, []

    token = os.environ.get(telegram.bot_token_env)
    chat_id = os.environ.get(telegram.chat_id_env)
    if not token or not chat_id:
        message = (
            f"Telegram skipped: set {telegram.bot_token_env} and "
            f"{telegram.chat_id_env} to auto-post."
        )
        if telegram.required:
            return 0, len(messages), [message]
        return 0, 0, [message]

    posted = 0
    failed = 0
    errors: list[str] = []
    for message in messages:
        try:
            send_telegram_message(token, chat_id, message, telegram)
            posted += 1
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            failed += 1
            errors.append(f"Telegram post failed: {exc}")
    return posted, failed, errors


def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    telegram: TelegramConfig,
) -> None:
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": str(telegram.disable_web_page_preview).lower(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(api_url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=telegram.timeout_seconds) as response:
        body = response.read().decode("utf-8")
        result = json.loads(body)
        if not result.get("ok"):
            raise ValueError(result)


def run_workflow(
    config: WorkflowConfig,
    dry_run: bool = False,
    skip_telegram: bool = False,
    skip_affiliate: bool = False,
    output_override: str | None = None,
) -> RunSummary:
    summary = RunSummary()
    all_deals: list[Deal] = []

    for feed in config.feeds:
        if not feed.enabled:
            summary.skipped_feeds.append(f"{feed.name}: disabled")
            continue
        feed_type = feed.type.lower()
        if feed_type not in ("manual", "inline", "cuelinks_offers") and not feed.url:
            summary.skipped_feeds.append(f"{feed.name}: missing feed URL")
            continue
        try:
            deals = parse_feed(feed)
            summary.fetched += len(deals)
            summary.feed_details.append(f"{feed.name}: parsed {len(deals)} deal(s)")
            all_deals.extend(deals)
        except Exception as exc:  # Keep other feeds moving if one source is unhealthy.
            summary.errors.append(f"{feed.name}: {exc}")

    accepted = filter_deals(all_deals, config.filters)
    affiliate_errors = [] if skip_affiliate else apply_affiliate_links(accepted, config.affiliate)
    summary.accepted = len(accepted)
    summary.skipped = summary.fetched - summary.accepted
    summary.errors.extend(affiliate_errors)
    if summary.fetched and not summary.accepted:
        summary.errors.append(
            f"All {summary.fetched} fetched deal(s) were filtered out. "
            "Check discount/price columns and filters.min_discount_percent / "
            "filters.require_discount_data in your config."
        )

    messages = [format_deal(deal, config.hashtags) for deal in accepted]
    output_file = Path(output_override or config.whatsapp.output_file)
    save_whatsapp_messages(messages, output_file)
    summary.whatsapp_file = str(output_file)

    if config.export_csv.enabled and accepted:
        csv_file = Path(config.export_csv.output_file)
        save_deals_csv(accepted, csv_file)
        summary.csv_file = str(csv_file)

    if skip_telegram:
        config.telegram.enabled = False
    posted, failed, errors = post_messages_to_telegram(messages, config.telegram, dry_run=dry_run)
    summary.telegram_posted = posted
    summary.telegram_failed = failed
    summary.errors.extend(errors)

    if (
        accepted
        and config.telegram.enabled
        and not skip_telegram
        and not dry_run
        and posted == 0
        and failed == 0
    ):
        summary.errors.append(
            "Telegram was not posted: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, "
            "or run with --dry-run for a test."
        )

    return summary



def apply_affiliate_links(deals: list[Deal], affiliate: AffiliateConfig) -> list[str]:
    if not affiliate.enabled:
        return []

    network = affiliate.network.lower()
    if network != "cuelinks":
        return [f"Unsupported affiliate network: {affiliate.network}"] if affiliate.required else []

    channel_id = affiliate.channel_id or os.environ.get(affiliate.channel_id_env, "")
    if not channel_id:
        message = f"Cuelinks skipped: set {affiliate.channel_id_env} to wrap deal URLs."
        return [message] if affiliate.required else []

    for deal in deals:
        deal.url = build_cuelinks_url(deal.url, channel_id, affiliate.source)
    return []


def build_cuelinks_url(url: str, channel_id: str, source: str = "linkkit") -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() == "linksredirect.com":
        return url
    query = urllib.parse.urlencode(
        {
            "cid": channel_id,
            "source": source or "linkkit",
            "url": url,
        }
    )
    return f"https://linksredirect.com/?{query}"

def first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def clean_text(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def clean_optional_text(value: Any) -> str | None:
    text = first_text(value)
    return clean_text(text) if text else None


def parse_money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def parse_percent(
    explicit_value: Any,
    title: str,
    original_price: float | None,
    price: float | None,
) -> float | None:
    explicit = parse_money(explicit_value)
    if explicit is not None:
        return explicit

    title_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", title)
    if title_match:
        percent = float(title_match.group(1))
        if 0 < percent <= 100:
            return percent

    if original_price and price is not None and original_price > price:
        return ((original_price - price) / original_price) * 100
    return None


def summarize(value: str | None, limit: int = 160) -> str | None:
    if not value:
        return None
    text = clean_text(re.sub(r"<[^>]+>", " ", value))
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def format_number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the semi-automated deals channel workflow.")
    parser.add_argument(
        "--config",
        default="config/auto-fetch-telegram.json",
        help="Path to workflow JSON config.",
    )
    parser.add_argument("--output", help="Override WhatsApp output file path.")
    parser.add_argument("--limit", type=int, help="Override max deals for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Build messages without posting to Telegram.")
    parser.add_argument("--skip-telegram", action="store_true", help="Disable Telegram posting for this run.")
    parser.add_argument("--skip-affiliate", action="store_true", help="Do not wrap deal URLs with affiliate tracking for this run.")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit successfully even when no deals were fetched or accepted.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print feed diagnostics to stderr.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(Path(args.config))
    if args.limit is not None:
        config.filters.max_items = args.limit

    summary = run_workflow(
        config,
        dry_run=args.dry_run,
        skip_telegram=args.skip_telegram,
        skip_affiliate=args.skip_affiliate,
        output_override=args.output,
    )
    if args.verbose:
        for detail in summary.feed_details:
            print(detail, file=sys.stderr)
        for skipped in summary.skipped_feeds:
            print(f"skipped: {skipped}", file=sys.stderr)
    print(json.dumps(dataclasses.asdict(summary), indent=2, sort_keys=True))

    if args.allow_empty:
        return 1 if summary.telegram_failed else 0

    if summary.telegram_failed:
        return 1
    if config.affiliate.required and not args.skip_affiliate and summary.errors:
        return 1
    if summary.skipped_feeds and summary.fetched == 0:
        return 1
    if summary.fetched == 0 or summary.accepted == 0:
        return 1
    if (
        config.telegram.enabled
        and not args.skip_telegram
        and not args.dry_run
        and summary.telegram_posted == 0
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
