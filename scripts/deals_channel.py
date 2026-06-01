#!/usr/bin/env python3
"""Semi-automated affiliate deals workflow.

Fetches affiliate feeds, filters weak deals, formats shareable messages, posts
to Telegram, and writes WhatsApp-ready copy without using a database.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
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


@dataclass
class FeedConfig:
    name: str
    url: str
    type: str = "auto"
    headers: dict[str, str] = field(default_factory=dict)
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
class WorkflowConfig:
    feeds: list[FeedConfig]
    filters: FilterConfig = field(default_factory=FilterConfig)
    hashtags: list[str] = field(default_factory=lambda: ["#deals"])
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)


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
    errors: list[str] = field(default_factory=list)


def load_config(path: Path) -> WorkflowConfig:
    with path.open("r", encoding="utf-8") as config_file:
        raw = json.load(config_file)

    feeds = [FeedConfig(**feed) for feed in raw.get("feeds", [])]
    if not feeds:
        raise ValueError("Config must include at least one feed.")

    return WorkflowConfig(
        feeds=feeds,
        filters=FilterConfig(**raw.get("filters", {})),
        hashtags=raw.get("hashtags", ["#deals"]),
        telegram=TelegramConfig(**raw.get("telegram", {})),
        whatsapp=WhatsAppConfig(**raw.get("whatsapp", {})),
    )


def fetch_text(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("", "file"):
        path = Path(urllib.parse.unquote(parsed.path if parsed.scheme else url))
        return path.read_text(encoding="utf-8")

    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


def parse_feed(feed: FeedConfig) -> list[Deal]:
    body = fetch_text(feed.url, feed.headers)
    feed_type = feed.type.lower()
    if feed_type == "auto":
        stripped = body.lstrip()
        feed_type = "json" if stripped.startswith(("{", "[")) else "rss"

    if feed_type == "json":
        return parse_json_feed(feed, body)
    if feed_type in ("rss", "atom", "xml"):
        return parse_xml_feed(feed, body)
    raise ValueError(f"Unsupported feed type for {feed.name}: {feed.type}")


def parse_json_feed(feed: FeedConfig, body: str) -> list[Deal]:
    payload = json.loads(body)
    items = extract_items(payload, feed.items_path)
    deals: list[Deal] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        title = first_text(
            get_nested(item, feed.title_field),
            get_nested(item, "name"),
            get_nested(item, "product_name"),
        )
        url = first_text(
            get_nested(item, feed.url_field),
            get_nested(item, "link"),
            get_nested(item, "deeplink"),
            get_nested(item, "affiliate_url"),
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
                coupon=clean_optional_text(get_nested(item, feed.coupon_field)),
                category=clean_optional_text(get_nested(item, feed.category_field)),
                description=clean_optional_text(get_nested(item, feed.description_field)),
                currency=feed.currency,
            )
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
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


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
    output_override: str | None = None,
) -> RunSummary:
    summary = RunSummary()
    all_deals: list[Deal] = []

    for feed in config.feeds:
        try:
            deals = parse_feed(feed)
            summary.fetched += len(deals)
            all_deals.extend(deals)
        except Exception as exc:  # Keep other feeds moving if one source is unhealthy.
            summary.errors.append(f"{feed.name}: {exc}")

    accepted = filter_deals(all_deals, config.filters)
    summary.accepted = len(accepted)
    summary.skipped = summary.fetched - summary.accepted

    messages = [format_deal(deal, config.hashtags) for deal in accepted]
    output_file = Path(output_override or config.whatsapp.output_file)
    save_whatsapp_messages(messages, output_file)
    summary.whatsapp_file = str(output_file)

    if skip_telegram:
        config.telegram.enabled = False
    posted, failed, errors = post_messages_to_telegram(messages, config.telegram, dry_run=dry_run)
    summary.telegram_posted = posted
    summary.telegram_failed = failed
    summary.errors.extend(errors)

    return summary


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
    parser.add_argument("--config", default="config/deals.json", help="Path to workflow JSON config.")
    parser.add_argument("--output", help="Override WhatsApp output file path.")
    parser.add_argument("--limit", type=int, help="Override max deals for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Build messages without posting to Telegram.")
    parser.add_argument("--skip-telegram", action="store_true", help="Disable Telegram posting for this run.")
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
        output_override=args.output,
    )
    print(json.dumps(dataclasses.asdict(summary), indent=2, sort_keys=True))

    if summary.telegram_failed:
        return 1
    if summary.fetched == 0 and summary.errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
