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
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

DEFAULT_PROMO_TEMPLATE = (
    "{merchant} : {title}\n"
    "\n"
    "{links}\n"
    "\n"
    "Apply Code : {coupon}\n"
    "\n"
    "{bank_offer}"
)
DEFAULT_CUELINKS_OFFERS_URLS = (
    "https://www.cuelinks.com/api/v2/offers.json",
    "https://www.cuelinks.com/api/v1/offers.json",
)
CUELINKS_ITEM_PATHS = ("offers", "data.offers", "data", "results", "items", "coupons", "deals")

# Domains for filters.allowed_merchants (keys are normalized with normalize_merchant_key).
MERCHANT_DOMAIN_MAP: dict[str, tuple[str, ...]] = {
    "amazon": ("amazon.in", "amazon.com", "amzn.to", "amzn.in"),
    "flipkart": ("flipkart.com", "fkrt.it", "fkrt.cc"),
    "myntra": ("myntra.com", "myntr.it", "myntra.in"),
    "meesho": ("meesho.com", "meesho.io"),
    "ajio": ("ajio.com",),
    "rare_rabbit": ("rarerabbit.com", "thehouseofrare.com", "houseofrare.com"),
    "lenskart": ("lenskart.com",),
    "nike": ("nike.com", "nike.in"),
    "woodland": ("woodlandworldwide.com",),
    "zomato": ("zomato.com",),
    "blinkit": ("blinkit.com", "grofers.com"),
    "swiggy": ("swiggy.com",),
    "zepto": ("zepto.com",),
    "bigbasket": ("bigbasket.com",),
    "rapido": ("rapido.bike", "rapido.app"),
    "uber": ("uber.com",),
    "ola": ("olacabs.com", "ola.com"),
    "fnp": ("fnp.com", "fernsnpetals.com"),
    "kfc": ("kfc.co.in", "kfc.com", "online.kfc.co.in"),
    "phonepe": ("phonepe.com",),
    "paytm": ("paytm.com",),
    "recharge": ("paytm.com", "phonepe.com"),
}

# Title/description hints → allowlist key (order: longer phrases first).
MERCHANT_TITLE_HINTS: tuple[tuple[str, str], ...] = (
    ("rare rabbit", "rare rabbit"),
    ("rare rabbit", "rarerabbit"),
    ("the house of rare", "rare rabbit"),
    ("bigbasket", "bigbasket"),
    ("big basket", "bigbasket"),
    ("phonepe", "phonepe"),
    ("phone pe", "phonepe"),
    ("paytm", "paytm"),
    ("ferns n petals", "fnp"),
    ("fernsnpetals", "fnp"),
    ("amazon", "amazon"),
    ("flipkart", "flipkart"),
    ("myntra", "myntra"),
    ("meesho", "meesho"),
    ("ajio", "ajio"),
    ("lenskart", "lenskart"),
    ("nike", "nike"),
    ("woodland", "woodland"),
    ("zomato", "zomato"),
    ("blinkit", "blinkit"),
    ("grofers", "blinkit"),
    ("swiggy", "swiggy"),
    ("instamart", "blinkit"),
    ("zepto", "zepto"),
    ("rapido", "rapido"),
    ("uber", "uber"),
    ("olacabs", "ola"),
    ("ola ", "ola"),
    ("kfc", "kfc"),
    ("recharge", "recharge"),
    ("bill pay", "recharge"),
    ("dth recharge", "recharge"),
)

AFFILIATE_ONLY_HOSTS = frozenset(
    {
        "linksredirect.com",
        "cuelinks.com",
        "cuelinks.in",
        "clnk.in",
        "clk.li",
    }
)


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
    image_field: str = "image_url"
    merchant_field: str = "merchant"
    links_field: str = "links"
    bank_offer_field: str = "bank_offer"
    message_field: str = "message"
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
class DedupeConfig:
    enabled: bool = True
    state_file: str = "out/posted_deals.json"
    max_entries: int = 10000
    record_on_dry_run: bool = False


@dataclass
class FilterConfig:
    min_discount_percent: float = 25.0
    min_savings_amount: float = 0.0
    require_discount_data: bool = False
    blocked_keywords: list[str] = field(default_factory=list)
    required_keywords: list[str] = field(default_factory=list)
    allowed_merchants: list[str] = field(default_factory=list)
    require_allowed_merchants: bool = False
    max_items: int = 10


@dataclass
class TelegramConfig:
    enabled: bool = True
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    disable_web_page_preview: bool = False
    send_photo_when_image_available: bool = True
    photo_caption_max_length: int = 1024
    timeout_seconds: int = 15
    required: bool = False


@dataclass
class WhatsAppConfig:
    output_file: str = "out/whatsapp_deals.txt"
    auto_send: bool = False
    access_token_env: str = "WHATSAPP_ACCESS_TOKEN"
    phone_number_id_env: str = "WHATSAPP_PHONE_NUMBER_ID"
    to_phone_env: str = "WHATSAPP_TO_PHONE"
    api_version: str = "v21.0"
    timeout_seconds: int = 15
    required: bool = False


@dataclass
class MessageFormatConfig:
    style: str = "compact"
    template: str = ""
    custom_message_field: str = "message"
    include_hashtags: bool = False


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
    dedupe: DedupeConfig = field(default_factory=DedupeConfig)
    message_format: MessageFormatConfig = field(default_factory=MessageFormatConfig)


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
    image_url: str | None = None
    merchant: str | None = None
    links_text: str | None = None
    bank_offer: str | None = None
    telegram_message: str | None = None
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
    whatsapp_posted: int = 0
    whatsapp_failed: int = 0
    whatsapp_file: str | None = None
    csv_file: str | None = None
    duplicates_skipped: int = 0
    run_duplicates_skipped: int = 0
    merchant_rejected: int = 0
    allowed_merchants_count: int = 0
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
    dedupe_raw = raw.get("dedupe", {})
    dedupe_fields = config_fields(DedupeConfig)
    message_format_raw = raw.get("message_format", {})
    message_format_fields = config_fields(MessageFormatConfig)
    whatsapp_raw = raw.get("whatsapp", {})
    whatsapp_fields = config_fields(WhatsAppConfig)

    filters_raw = dict(raw.get("filters", {}))
    merchants_file = raw.get("allowed_merchants_file")
    if merchants_file and not filters_raw.get("allowed_merchants"):
        merchants_path = Path(merchants_file)
        if not merchants_path.is_absolute():
            merchants_path = path.parent / merchants_path
        with merchants_path.open("r", encoding="utf-8") as merchants_handle:
            merchants_payload = json.load(merchants_handle)
        filters_raw["allowed_merchants"] = merchants_payload.get("allowed_merchants", [])

    filters = FilterConfig(**filters_raw)
    if filters.require_allowed_merchants and not filters.allowed_merchants:
        raise ValueError(
            "Merchant filter is required but allowed_merchants is empty. "
            "Set allowed_merchants_file in config (e.g. config/allowed-merchants.json) "
            "or merge the latest attractivedeals PR with merchant filtering."
        )

    return WorkflowConfig(
        feeds=feeds,
        filters=filters,
        hashtags=raw.get("hashtags", ["#deals"]),
        affiliate=AffiliateConfig(**raw.get("affiliate", {})),
        telegram=TelegramConfig(**raw.get("telegram", {})),
        whatsapp=WhatsAppConfig(**{k: v for k, v in whatsapp_raw.items() if k in whatsapp_fields}),
        export_csv=ExportCsvConfig(**{k: v for k, v in export_raw.items() if k in export_fields}),
        dedupe=DedupeConfig(**{k: v for k, v in dedupe_raw.items() if k in dedupe_fields}),
        message_format=MessageFormatConfig(
            **{k: v for k, v in message_format_raw.items() if k in message_format_fields}
        ),
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
        links_text = clean_optional_text(
            first_text(
                get_nested(item, feed.links_field),
                get_nested(item, "link_lines"),
                get_nested(item, "links_text"),
            )
        )
        telegram_message = clean_optional_text(
            first_text(
                get_nested(item, feed.message_field),
                get_nested(item, "telegram_message"),
                get_nested(item, "post_text"),
            )
        )
        bank_offer = clean_optional_text(
            first_text(
                get_nested(item, feed.bank_offer_field),
                get_nested(item, "bank_offer"),
                get_nested(item, "bank_offers"),
            )
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
        if not url:
            url = first_url_in_text(links_text) or first_url_in_text(telegram_message)
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

        image_url = clean_optional_text(
            first_text(
                get_nested(item, feed.image_field),
                get_nested(item, "image"),
                get_nested(item, "image_url"),
                get_nested(item, "thumbnail"),
                get_nested(item, "product_image"),
                get_nested(item, "banner_image"),
                get_nested(item, "offer_image"),
            )
        )

        merchant = clean_optional_text(
            first_text(
                get_nested(item, feed.merchant_field),
                get_nested(item, "merchant_name"),
                get_nested(item, "advertiser_name"),
                get_nested(item, "advertiser"),
                get_nested(item, "store"),
                get_nested(item, "store_name"),
                get_nested(item, "brand"),
                get_nested(item, "campaign_name"),
            )
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
                image_url=image_url,
                merchant=merchant,
                links_text=links_text,
                bank_offer=bank_offer,
                telegram_message=telegram_message,
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
    if "img" in normalized and "image_url" not in normalized:
        normalized["image_url"] = normalized["img"]
    if "image" in normalized and "image_url" not in normalized:
        normalized["image_url"] = normalized["image"]
    if "store" in normalized and "merchant" not in normalized:
        normalized["merchant"] = normalized["store"]
    if "brand" in normalized and "merchant" not in normalized:
        normalized["merchant"] = normalized["brand"]
    if "link_lines" in normalized and "links" not in normalized:
        normalized["links"] = normalized["link_lines"]
    if "post_text" in normalized and "message" not in normalized:
        normalized["message"] = normalized["post_text"]
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


def normalize_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:160]


def is_promo_style_deal(deal: Deal) -> bool:
    return bool((deal.links_text or deal.telegram_message or "").strip())


def merchant_deal_key(deal: Deal) -> str:
    """Stable key for dedupe (unwraps affiliate redirects; promo = merchant + title)."""
    primary_url = deal.url
    for chunk in (deal.links_text, deal.telegram_message):
        if chunk:
            first = first_url_in_text(chunk)
            if first:
                primary_url = first
                break
    unwrapped = unwrap_deal_url(primary_url)
    url_part = normalize_deal_url(unwrapped)
    title_part = normalize_title_key(deal.title)
    merchant_part = normalize_merchant_key(deal.merchant or "")
    if not merchant_part and url_part:
        host = deal_host(primary_url)
        for key, domains in MERCHANT_DOMAIN_MAP.items():
            if any(host == d or host.endswith(f".{d}") for d in domains):
                merchant_part = key
                break

    if is_promo_style_deal(deal) and title_part:
        base = merchant_part or "promo"
        return f"promo|{base}|{title_part}"
    # Same store + same headline = one campaign (stops API + sheet double-posting).
    if merchant_part and title_part:
        return f"camp|{merchant_part}|{title_part}"
    if title_part and url_part:
        return f"{title_part}|{url_part}"
    return url_part or title_part or deal.title.lower()


def filter_deals(deals: list[Deal], filters: FilterConfig) -> tuple[list[Deal], int, int]:
    accepted: list[Deal] = []
    seen: set[str] = set()
    merchant_rejected = 0
    run_duplicates_skipped = 0

    for deal in deals:
        key = merchant_deal_key(deal)
        if key in seen:
            run_duplicates_skipped += 1
            continue
        seen.add(key)

        if not is_allowed_by_keywords(deal, filters):
            continue
        if not is_allowed_merchant(deal, filters):
            if filters.allowed_merchants:
                merchant_rejected += 1
            continue
        if not is_strong_enough(deal, filters):
            continue
        accepted.append(deal)
        if filters.max_items and len(accepted) >= filters.max_items:
            break

    return accepted, merchant_rejected, run_duplicates_skipped


def normalize_deal_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", "")
    )


def deal_key(deal: Deal) -> str:
    normalized_url = normalize_deal_url(deal.url)
    return normalized_url or deal.title.lower()


def load_posted_keys(state_file: Path) -> list[str]:
    if not state_file.exists():
        return []
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        keys = payload.get("keys", [])
        if isinstance(keys, list):
            return [str(key) for key in keys if key]
    return []


def save_posted_keys(state_file: Path, keys: list[str], max_entries: int) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    trimmed = keys[-max_entries:] if max_entries > 0 else keys
    state_file.write_text(json.dumps({"keys": trimmed}, indent=2) + "\n", encoding="utf-8")


def filter_already_posted(
    deals: list[Deal],
    posted_keys: list[str],
) -> tuple[list[Deal], int]:
    posted_set = set(posted_keys)
    fresh: list[Deal] = []
    skipped = 0
    for deal in deals:
        if merchant_deal_key(deal) in posted_set:
            skipped += 1
            continue
        fresh.append(deal)
    return fresh, skipped


def mark_deals_posted(
    state_file: Path,
    deals: list[Deal],
    max_entries: int,
) -> None:
    if not deals:
        return
    keys = load_posted_keys(state_file)
    known = set(keys)
    for deal in deals:
        key = merchant_deal_key(deal)
        if key in known:
            continue
        keys.append(key)
        known.add(key)
    save_posted_keys(state_file, keys, max_entries)


def normalize_merchant_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def resolve_allowed_domains(allowed_merchants: list[str]) -> set[str]:
    domains: set[str] = set()
    for merchant in allowed_merchants:
        key = normalize_merchant_key(merchant)
        mapped = MERCHANT_DOMAIN_MAP.get(key)
        if mapped:
            domains.update(mapped)
            continue
        if "." in merchant:
            domains.add(merchant.lower().strip())
    return domains


def unwrap_deal_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host in ("linksredirect.com", "cuelinks.com", "clnk.in", "clk.li", "cuelinks.in"):
        embedded = urllib.parse.parse_qs(parsed.query).get("url", [None])[0]
        if embedded:
            return urllib.parse.unquote(embedded)
    return url


def deal_host(url: str) -> str:
    return urllib.parse.urlparse(unwrap_deal_url(url)).netloc.lower().removeprefix("www.")


def host_matches_allowed(url: str, allowed_domains: set[str]) -> bool:
    if not allowed_domains:
        return True
    host = deal_host(url)
    for domain in allowed_domains:
        candidate = domain.lower().removeprefix("www.")
        if host == candidate or host.endswith(f".{candidate}"):
            return True
    return False


def merchant_label_matches_allowed(label: str, allowed_merchants: list[str]) -> bool:
    key = normalize_merchant_key(label)
    if not key:
        return False
    for merchant in allowed_merchants:
        candidate = normalize_merchant_key(merchant)
        if key == candidate or key in candidate or candidate in key:
            return True
    return False


def first_url_in_text(text: str | None) -> str | None:
    if not text:
        return None
    match = URL_IN_TEXT_RE.search(text)
    return match.group(0) if match else None


def urls_in_deal_text(deal: Deal) -> list[str]:
    found: list[str] = []
    for chunk in (deal.url, deal.links_text, deal.telegram_message, deal.description):
        if chunk:
            found.extend(URL_IN_TEXT_RE.findall(chunk))
    deduped: list[str] = []
    seen: set[str] = set()
    for url in found:
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def merchant_key_from_host(host: str) -> str | None:
    for key, domains in MERCHANT_DOMAIN_MAP.items():
        for domain in domains:
            candidate = domain.lower().removeprefix("www.")
            if host == candidate or host.endswith(f".{candidate}"):
                return key
    return None


def infer_merchant_key_from_text(text: str) -> str | None:
    lowered = text.lower()
    for hint, key in MERCHANT_TITLE_HINTS:
        if hint in lowered:
            return key
    return None


def resolve_deal_merchant_key(deal: Deal) -> str | None:
    if deal.merchant:
        key = normalize_merchant_key(deal.merchant)
        if key in MERCHANT_DOMAIN_MAP:
            return key
        for allowed_key in MERCHANT_DOMAIN_MAP:
            if merchant_label_matches_allowed(deal.merchant, [allowed_key]):
                return allowed_key

    for url in urls_in_deal_text(deal):
        host = deal_host(url)
        if host in AFFILIATE_ONLY_HOSTS:
            continue
        from_host = merchant_key_from_host(host)
        if from_host:
            return from_host

    text = " ".join(
        part for part in [deal.title, deal.description or "", deal.category or ""] if part
    )
    from_title = infer_merchant_key_from_text(text)
    if from_title:
        return from_title

    for url in urls_in_deal_text(deal):
        host = deal_host(url)
        from_host = merchant_key_from_host(host)
        if from_host:
            return from_host
    return None


def is_allowed_merchant(deal: Deal, filters: FilterConfig) -> bool:
    """Only famous-brand offers pass. Junk merchants never post."""
    if not filters.allowed_merchants:
        return True

    brand_key = resolve_deal_merchant_key(deal)
    if not brand_key or not merchant_label_matches_allowed(brand_key, filters.allowed_merchants):
        return False

    allowed_domains = resolve_allowed_domains(filters.allowed_merchants)
    for url in urls_in_deal_text(deal):
        if host_matches_allowed(url, allowed_domains):
            return True

    host = deal_host(deal.url)
    if host in AFFILIATE_ONLY_HOSTS or not host:
        return True

    return merchant_key_from_host(host) == brand_key


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


def apply_template(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return collapse_extra_blank_lines(result)


def collapse_extra_blank_lines(text: str) -> str:
    lines = text.splitlines()
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            cleaned.append(line.rstrip())
            blank = False
        elif not blank:
            cleaned.append("")
            blank = True
    return "\n".join(cleaned).strip()


def format_merchant_display(merchant: str | None) -> str:
    if not merchant:
        return "Deal"
    cleaned = merchant.strip()
    if cleaned.lower() == "myntra":
        return "Myntra"
    return cleaned[:1].upper() + cleaned[1:]


def format_promo_deal(deal: Deal, template: str) -> str:
    merchant = format_merchant_display(deal.merchant)
    links = (deal.links_text or "").strip()
    if not links and deal.url:
        links = deal.url
    coupon = (deal.coupon or "").strip()
    bank_offer = (deal.bank_offer or "").strip() or summarize(deal.description) or ""

    if template.strip() and template.strip() != DEFAULT_PROMO_TEMPLATE.strip():
        return collapse_extra_blank_lines(
            apply_template(
                template,
                {
                    "merchant": merchant,
                    "title": deal.title.strip(),
                    "links": links,
                    "coupon": coupon,
                    "bank_offer": bank_offer,
                },
            )
        )

    lines = [f"{merchant} : {deal.title.strip()}"]
    if links:
        lines.extend(["", links])
    if coupon:
        lines.extend(["", f"Apply Code : {coupon}"])
    if bank_offer:
        lines.extend(["", bank_offer])
    return "\n".join(lines).strip()


def format_deal_message(
    deal: Deal,
    hashtags: list[str],
    message_format: MessageFormatConfig,
) -> str:
    if deal.telegram_message:
        return deal.telegram_message.strip()

    style = (message_format.style or "compact").lower()
    if style == "promo":
        template = message_format.template.strip() or DEFAULT_PROMO_TEMPLATE
        message = format_promo_deal(deal, template)
        if message_format.include_hashtags:
            tags = build_hashtags(hashtags, deal.category)
            if tags:
                message = f"{message}\n\n{' '.join(tags)}"
        return message

    return format_deal(deal, hashtags)


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
        "image_url",
        "merchant",
        "links",
        "bank_offer",
        "message",
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
                    "image_url": deal.image_url or "",
                    "merchant": deal.merchant or "",
                    "links": deal.links_text or "",
                    "bank_offer": deal.bank_offer or "",
                    "message": deal.telegram_message or "",
                }
            )


def post_messages_to_telegram(
    messages: list[str],
    telegram: TelegramConfig,
    dry_run: bool = False,
    deals: list[Deal] | None = None,
) -> tuple[int, int, list[str], list[Deal]]:
    if dry_run or not telegram.enabled:
        return 0, 0, [], []

    token = os.environ.get(telegram.bot_token_env)
    chat_id = os.environ.get(telegram.chat_id_env)
    if not token or not chat_id:
        message = (
            f"Telegram skipped: set {telegram.bot_token_env} and "
            f"{telegram.chat_id_env} to auto-post."
        )
        if telegram.required:
            return 0, len(messages), [message], []
        return 0, 0, [message], []

    posted = 0
    failed = 0
    errors: list[str] = []
    posted_deals: list[Deal] = []
    for index, message in enumerate(messages):
        deal = deals[index] if deals and index < len(deals) else None
        try:
            if (
                deal
                and deal.image_url
                and telegram.send_photo_when_image_available
            ):
                send_telegram_photo(token, chat_id, deal.image_url, message, telegram)
            else:
                send_telegram_message(token, chat_id, message, telegram)
            posted += 1
            if deal:
                posted_deals.append(deal)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            failed += 1
            errors.append(f"Telegram post failed: {exc}")
    return posted, failed, errors, posted_deals


def truncate_telegram_caption(text: str, max_length: int) -> str:
    if max_length <= 0 or len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3] + "..."


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


def send_telegram_photo(
    token: str,
    chat_id: str,
    photo_url: str,
    caption: str,
    telegram: TelegramConfig,
) -> None:
    api_url = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": truncate_telegram_caption(caption, telegram.photo_caption_max_length),
        }
    ).encode("utf-8")
    request = urllib.request.Request(api_url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=telegram.timeout_seconds) as response:
            body = response.read().decode("utf-8")
            result = json.loads(body)
            if not result.get("ok"):
                raise ValueError(result)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        send_telegram_message(token, chat_id, caption, telegram)


def normalize_whatsapp_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError(f"Invalid WhatsApp phone number: {value!r}")
    return digits


def send_whatsapp_message(
    access_token: str,
    phone_number_id: str,
    to_phone: str,
    text: str,
    whatsapp: WhatsAppConfig,
) -> None:
    api_url = (
        f"https://graph.facebook.com/{whatsapp.api_version}/"
        f"{phone_number_id}/messages"
    )
    body = json.dumps(
        {
            "messaging_product": "whatsapp",
            "to": normalize_whatsapp_phone(to_phone),
            "type": "text",
            "text": {"preview_url": True, "body": text},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=whatsapp.timeout_seconds) as response:
        result = json.loads(response.read().decode("utf-8"))
        if "error" in result:
            raise ValueError(result["error"])


def post_messages_to_whatsapp(
    messages: list[str],
    whatsapp: WhatsAppConfig,
    dry_run: bool = False,
    deals: list[Deal] | None = None,
) -> tuple[int, int, list[str], list[Deal]]:
    if dry_run or not whatsapp.auto_send:
        return 0, 0, [], []

    token = os.environ.get(whatsapp.access_token_env, "").strip()
    phone_number_id = os.environ.get(whatsapp.phone_number_id_env, "").strip()
    to_phone = os.environ.get(whatsapp.to_phone_env, "").strip()
    if not token or not phone_number_id or not to_phone:
        message = (
            f"WhatsApp auto-send skipped: set {whatsapp.access_token_env}, "
            f"{whatsapp.phone_number_id_env}, and {whatsapp.to_phone_env}."
        )
        if whatsapp.required:
            return 0, len(messages), [message], []
        return 0, 0, [message], []

    posted = 0
    failed = 0
    errors: list[str] = []
    posted_deals: list[Deal] = []
    for index, message in enumerate(messages):
        try:
            send_whatsapp_message(token, phone_number_id, to_phone, message, whatsapp)
            posted += 1
            if deals and index < len(deals):
                posted_deals.append(deals[index])
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            failed += 1
            errors.append(f"WhatsApp post failed: {exc}")
    return posted, failed, errors, posted_deals


def run_workflow(
    config: WorkflowConfig,
    dry_run: bool = False,
    skip_telegram: bool = False,
    skip_whatsapp: bool = False,
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

    summary.allowed_merchants_count = len(config.filters.allowed_merchants)
    filtered, summary.merchant_rejected, summary.run_duplicates_skipped = filter_deals(
        all_deals, config.filters
    )
    summary.skipped = summary.fetched - len(filtered)
    if summary.run_duplicates_skipped:
        summary.feed_details.append(
            f"same-run dedupe: skipped {summary.run_duplicates_skipped} duplicate deal(s)"
        )
    if config.filters.allowed_merchants:
        summary.feed_details.append(
            f"brand-only filter: {summary.allowed_merchants_count} allowed store(s), "
            f"{len(filtered)} offer(s) kept"
        )
    if summary.merchant_rejected:
        summary.feed_details.append(
            f"merchant filter: rejected {summary.merchant_rejected} deal(s) "
            f"(not on allowed store domains)"
        )

    to_publish = filtered
    if config.dedupe.enabled:
        state_file = Path(config.dedupe.state_file)
        posted_keys = load_posted_keys(state_file)
        to_publish, summary.duplicates_skipped = filter_already_posted(filtered, posted_keys)
        if summary.duplicates_skipped:
            summary.feed_details.append(
                f"cross-run dedupe: skipped {summary.duplicates_skipped} already posted deal(s)"
            )

    affiliate_errors = [] if skip_affiliate else apply_affiliate_links(to_publish, config.affiliate)
    summary.accepted = len(to_publish)
    summary.errors.extend(affiliate_errors)
    if summary.fetched and not filtered:
        summary.errors.append(
            f"All {summary.fetched} fetched deal(s) were filtered out. "
            "Check discount/price columns and filters.min_discount_percent / "
            "filters.require_discount_data in your config."
        )

    messages = [
        format_deal_message(deal, config.hashtags, config.message_format) for deal in to_publish
    ]
    output_file = Path(output_override or config.whatsapp.output_file)
    save_whatsapp_messages(messages, output_file)
    summary.whatsapp_file = str(output_file)

    if config.export_csv.enabled and to_publish:
        csv_file = Path(config.export_csv.output_file)
        save_deals_csv(to_publish, csv_file)
        summary.csv_file = str(csv_file)

    if skip_telegram:
        config.telegram.enabled = False
    posted, failed, errors, posted_deals = post_messages_to_telegram(
        messages,
        config.telegram,
        dry_run=dry_run,
        deals=to_publish,
    )
    summary.telegram_posted = posted
    summary.telegram_failed = failed
    summary.errors.extend(errors)

    if skip_whatsapp:
        config.whatsapp.auto_send = False
    wa_posted, wa_failed, wa_errors, wa_posted_deals = post_messages_to_whatsapp(
        messages,
        config.whatsapp,
        dry_run=dry_run,
        deals=to_publish,
    )
    summary.whatsapp_posted = wa_posted
    summary.whatsapp_failed = wa_failed
    summary.errors.extend(wa_errors)

    all_posted_deals = posted_deals + [
        deal
        for deal in wa_posted_deals
        if merchant_deal_key(deal) not in {merchant_deal_key(item) for item in posted_deals}
    ]
    should_record = config.dedupe.enabled and (
        (not dry_run and all_posted_deals)
        or (dry_run and config.dedupe.record_on_dry_run and to_publish)
    )
    if should_record:
        record_deals = all_posted_deals if not dry_run else to_publish
        mark_deals_posted(Path(config.dedupe.state_file), record_deals, config.dedupe.max_entries)

    if (
        to_publish
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

    if (
        to_publish
        and config.whatsapp.auto_send
        and not skip_whatsapp
        and not dry_run
        and wa_posted == 0
        and wa_failed == 0
    ):
        summary.errors.append(
            "WhatsApp was not sent: set WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, "
            "and WHATSAPP_TO_PHONE, or disable whatsapp.auto_send."
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
        if deal.links_text:
            deal.links_text = wrap_urls_in_text(deal.links_text, channel_id, affiliate.source)
        if deal.telegram_message:
            deal.telegram_message = wrap_urls_in_text(
                deal.telegram_message, channel_id, affiliate.source
            )
        if deal.description:
            deal.description = wrap_urls_in_text(deal.description, channel_id, affiliate.source)
    return []


def wrap_urls_in_text(text: str, channel_id: str, source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        if urllib.parse.urlparse(original).netloc.lower() == "linksredirect.com":
            return original
        return build_cuelinks_url(original, channel_id, source)

    return URL_IN_TEXT_RE.sub(replace, text)


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
        default="config/brands-only-telegram.json",
        help="Path to workflow JSON config.",
    )
    parser.add_argument("--output", help="Override WhatsApp output file path.")
    parser.add_argument("--limit", type=int, help="Override max deals for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Build messages without posting to Telegram.")
    parser.add_argument("--skip-telegram", action="store_true", help="Disable Telegram posting for this run.")
    parser.add_argument(
        "--skip-whatsapp",
        action="store_true",
        help="Disable WhatsApp Cloud API auto-send for this run.",
    )
    parser.add_argument("--skip-affiliate", action="store_true", help="Do not wrap deal URLs with affiliate tracking for this run.")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit successfully even when no deals were fetched or accepted.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print feed diagnostics to stderr.")
    parser.add_argument(
        "--reset-posted",
        action="store_true",
        help="Clear cross-run dedupe state before running.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(Path(args.config))
    if args.limit is not None:
        config.filters.max_items = args.limit
    if args.reset_posted:
        state_file = Path(config.dedupe.state_file)
        if state_file.exists():
            state_file.unlink()

    summary = run_workflow(
        config,
        dry_run=args.dry_run,
        skip_telegram=args.skip_telegram,
        skip_whatsapp=args.skip_whatsapp,
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

    if summary.telegram_failed or summary.whatsapp_failed:
        return 1
    if config.affiliate.required and not args.skip_affiliate and summary.errors:
        return 1
    if summary.skipped_feeds and summary.fetched == 0:
        return 1
    if summary.fetched == 0:
        return 1
    if summary.accepted == 0:
        if summary.duplicates_skipped > 0:
            return 1 if summary.telegram_failed else 0
        return 1
    if (
        config.telegram.enabled
        and not args.skip_telegram
        and not args.dry_run
        and summary.telegram_posted == 0
        and summary.duplicates_skipped == 0
    ):
        return 1
    if (
        config.whatsapp.auto_send
        and not args.skip_whatsapp
        and not args.dry_run
        and summary.whatsapp_posted == 0
        and summary.duplicates_skipped == 0
        and config.whatsapp.required
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
