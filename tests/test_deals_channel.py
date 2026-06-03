import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from scripts.deals_channel import (
    AffiliateConfig,
    DedupeConfig,
    ExportCsvConfig,
    FeedConfig,
    FilterConfig,
    MessageFormatConfig,
    TelegramConfig,
    WhatsAppConfig,
    WorkflowConfig,
    build_cuelinks_url,
    discover_cuelinks_items,
    normalize_whatsapp_phone,
    fetch_cuelinks_offers_from_url,
    filter_deals,
    format_deal_message,
    host_matches_allowed,
    load_config,
    format_deal,
    resolve_allowed_domains,
    unwrap_deal_url,
    wrap_urls_in_text,
    main,
    mark_deals_posted,
    merchant_deal_key,
    parse_feed,
    run_workflow,
    save_deals_csv,
)


class DealsChannelTests(unittest.TestCase):
    def test_format_promo_deal_matches_channel_style(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        deal = Deal(
            source="sheet",
            title="Upto 50% Off On Nike + Extra 10% Code + 10% Bank Offer.",
            url="https://myntr.it/5axwTbN",
            merchant="myntra",
            links_text=(
                "Men : https://myntr.it/5axwTbN\n"
                "Women : https://myntr.it/olGTq7m"
            ),
            coupon="NIKEPREPAID10",
            bank_offer="+ 10% Off With HDFC CC (Min. ₹3500)",
        )
        message = format_deal_message(
            deal,
            ["#deals"],
            MessageFormatConfig(style="promo", include_hashtags=False),
        )
        self.assertIn("Myntra : Upto 50% Off On Nike", message)
        self.assertIn("Men : https://myntr.it/5axwTbN", message)
        self.assertIn("Apply Code : NIKEPREPAID10", message)
        self.assertIn("HDFC CC", message)

    def test_custom_message_column_overrides_template(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        deal = Deal(
            source="sheet",
            title="ignored",
            url="https://www.flipkart.com/p/1",
            telegram_message="Custom post body with https://www.flipkart.com/p/1",
        )
        message = format_deal_message(
            deal,
            [],
            MessageFormatConfig(style="promo"),
        )
        self.assertEqual(message, "Custom post body with https://www.flipkart.com/p/1")

    def test_wrap_urls_in_text_rewrites_each_link(self):
        wrapped = wrap_urls_in_text(
            "Men : https://myntr.it/abc\nWomen : https://myntr.it/def",
            "12345",
            "linkkit",
        )
        self.assertEqual(wrapped.count("linksredirect.com"), 2)

    def test_load_config_reads_allowed_merchants_file(self):
        config = load_config(Path("config/merchant-allowlist-telegram.json"))
        self.assertIn("amazon", [m.lower() for m in config.filters.allowed_merchants])
        self.assertIn("flipkart", [m.lower() for m in config.filters.allowed_merchants])

    def test_allowed_merchants_filter_by_domain(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        filters = FilterConfig(
            allowed_merchants=["flipkart", "amazon"],
            min_discount_percent=0,
            require_discount_data=False,
        )
        flipkart = Deal(
            source="t",
            title="Shoes sale",
            url="https://www.flipkart.com/p/abc",
            discount_percent=30,
        )
        other = Deal(
            source="t",
            title="Random sale",
            url="https://www.example.com/deal",
            discount_percent=30,
        )
        accepted, rejected, _run_dup = filter_deals([flipkart, other], filters)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].url, flipkart.url)
        self.assertEqual(rejected, 1)

    def test_allowed_merchants_rejects_wrong_merchant_column(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        row = Deal(
            source="sheet",
            title="Nykaa lipstick sale",
            url="https://www.nykaa.com/product/1",
            merchant="nykaa",
            discount_percent=40,
        )
        accepted, rejected, _run_dup = filter_deals(
            [row],
            FilterConfig(
                allowed_merchants=["flipkart", "amazon"],
                min_discount_percent=0,
                require_discount_data=False,
            ),
        )
        self.assertEqual(len(accepted), 0)
        self.assertEqual(rejected, 1)

    def test_same_run_dedupe_by_merchant_and_title(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        first = Deal(
            source="api",
            title="Nike 50% off sale",
            url="https://www.myntra.com/deal-a",
            merchant="myntra",
        )
        second = Deal(
            source="sheet",
            title="Nike 50% off sale",
            url="https://www.myntra.com/deal-b",
            merchant="myntra",
        )
        accepted, _rejected, run_dup = filter_deals(
            [first, second],
            FilterConfig(
                allowed_merchants=["myntra"],
                min_discount_percent=0,
                require_discount_data=False,
            ),
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(run_dup, 1)

    def test_allowed_merchants_accepts_flipkart_title_with_tracking_url(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        deal = Deal(
            source="cuelinks-offers",
            title="Flipkart Big Billion Days 50% off",
            url="https://linksredirect.com/?cid=1&source=linkkit",
            merchant="",
        )
        accepted, rejected, _ = filter_deals(
            [deal],
            FilterConfig(
                allowed_merchants=["flipkart", "amazon"],
                min_discount_percent=0,
                require_discount_data=False,
            ),
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, 0)

    def test_allowed_merchants_rejects_title_only_match(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        fake = Deal(
            source="t",
            title="Amazon mega sale today",
            url="https://www.someotherstore.com/deal",
            discount_percent=50,
        )
        accepted, rejected, _run_dup = filter_deals(
            [fake],
            FilterConfig(
                allowed_merchants=["amazon"],
                min_discount_percent=0,
                require_discount_data=False,
            ),
        )
        self.assertEqual(len(accepted), 0)
        self.assertEqual(rejected, 1)

    def test_allowed_merchants_unwrap_cuelinks_redirect(self):
        deal_mod = __import__("scripts.deals_channel", fromlist=["Deal"])
        Deal = deal_mod.Deal
        embedded = "https://www.myntra.com/deal/1"
        wrapped = (
            "https://linksredirect.com/?cid=1&source=linkkit&url="
            + urllib.parse.quote(embedded, safe="")
        )
        self.assertEqual(unwrap_deal_url(wrapped), embedded)
        domains = resolve_allowed_domains(["myntra"])
        self.assertTrue(host_matches_allowed(wrapped, domains))

    def test_csv_feed_parses_image_url_column(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "sheet.csv"
            feed_file.write_text(
                "title,url,price,original_price,image_url\n"
                "Watch 40% off,https://www.flipkart.com/watch,600,1000,"
                "https://cdn.example.com/watch.jpg\n",
                encoding="utf-8",
            )
            parsed = parse_feed(
                FeedConfig(name="google-sheet", url=str(feed_file), type="csv", currency="Rs. ")
            )
            self.assertEqual(parsed[0].image_url, "https://cdn.example.com/watch.jpg")

    def test_json_feed_is_parsed_and_weak_deals_are_filtered(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "feed.json"
            feed_file.write_text(
                json.dumps(
                    {
                        "deals": [
                            {
                                "title": "Laptop 40% off",
                                "affiliate_url": "https://example.com/laptop?ref=abc",
                                "price": "60000",
                                "mrp": "100000",
                                "coupon_code": "SAVE40",
                                "category": "Electronics",
                            },
                            {
                                "title": "Cable 5% off",
                                "affiliate_url": "https://example.com/cable",
                                "price": "95",
                                "mrp": "100",
                            },
                            {
                                "title": "Used phone 80% off",
                                "affiliate_url": "https://example.com/phone",
                                "price": "2000",
                                "mrp": "10000",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            feed = FeedConfig(
                name="local-json",
                url=str(feed_file),
                type="json",
                items_path="deals",
                url_field="affiliate_url",
                original_price_field="mrp",
                coupon_field="coupon_code",
                currency="Rs. ",
            )

            parsed = parse_feed(feed)
            accepted, _rejected, _run_dup = filter_deals(
                parsed,
                FilterConfig(
                    min_discount_percent=25,
                    min_savings_amount=100,
                    require_discount_data=True,
                    blocked_keywords=["used"],
                    max_items=10,
                ),
            )

            self.assertEqual(len(parsed), 3)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0].title, "Laptop 40% off")
            self.assertEqual(accepted[0].discount_percent, 40)

    def test_format_deal_adds_price_coupon_and_hashtags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "feed.json"
            feed_file.write_text(
                json.dumps(
                    [
                        {
                            "title": "Headphones 50% off",
                            "url": "https://example.com/headphones",
                            "price": "999",
                            "original_price": "1998",
                            "coupon": "AUDIO50",
                            "category": "Audio Gear",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            deal = parse_feed(FeedConfig(name="local", url=str(feed_file), type="json", currency="Rs. "))[0]

            message = format_deal_message(
                deal, ["#Deals", "Top Picks"], MessageFormatConfig(style="compact")
            )

            self.assertIn("🔥 Headphones 50% off", message)
            self.assertIn("Price: Rs. 999 (was Rs. 1,998)", message)
            self.assertIn("Coupon: AUDIO50", message)
            self.assertIn("#deals #toppicks #audiogear", message)

    def test_run_workflow_writes_whatsapp_file_without_telegram(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "feed.json"
            output_file = Path(tmp_dir) / "whatsapp.txt"
            feed_file.write_text(
                json.dumps(
                    [
                        {
                            "title": "Mixer 30% off",
                            "url": "https://example.com/mixer",
                            "price": "700",
                            "original_price": "1000",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            config = WorkflowConfig(
                feeds=[FeedConfig(name="local", url=str(feed_file), type="json", currency="Rs. ")],
                filters=FilterConfig(min_discount_percent=25, require_discount_data=True),
                telegram=TelegramConfig(enabled=False),
                whatsapp=WhatsAppConfig(output_file=str(output_file)),
            )

            summary = run_workflow(config, skip_telegram=True)

            self.assertEqual(summary.fetched, 1)
            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.telegram_posted, 0)
            self.assertTrue(output_file.exists())
            self.assertIn("Mixer 30% off", output_file.read_text(encoding="utf-8"))

    def test_normalize_whatsapp_phone_strips_formatting(self):
        self.assertEqual(normalize_whatsapp_phone("+91 98765-43210"), "919876543210")

    def test_merchant_deal_key_unwraps_cuelinks_redirect(self):
        deal = __import__("scripts.deals_channel", fromlist=["Deal"]).Deal(
            source="t",
            title="Deal",
            url="https://linksredirect.com/?cid=1&url=https%3A%2F%2Fwww.flipkart.com%2Fp%2F123",
        )
        self.assertEqual(
            merchant_deal_key(deal),
            "camp|flipkart|deal",
        )

    def test_cross_run_dedupe_skips_previously_posted_deal(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = Path(tmp_dir) / "posted.json"
            whatsapp_file = Path(tmp_dir) / "whatsapp.txt"
            mark_deals_posted(
                state_file,
                [
                    __import__("scripts.deals_channel", fromlist=["Deal"]).Deal(
                        source="t",
                        title="Old deal 40% off",
                        url="https://www.flipkart.com/p/123",
                    )
                ],
                max_entries=100,
            )
            config = WorkflowConfig(
                feeds=[
                    FeedConfig(
                        name="manual",
                        type="manual",
                        items=[
                            {
                                "title": "Old deal 40% off",
                                "url": "https://www.flipkart.com/p/123",
                                "price": "600",
                                "original_price": "1000",
                            },
                            {
                                "title": "New deal 50% off",
                                "url": "https://www.ajio.com/p/456",
                                "price": "500",
                                "original_price": "1000",
                            },
                        ],
                    )
                ],
                filters=FilterConfig(min_discount_percent=20, require_discount_data=True, max_items=10),
                telegram=TelegramConfig(enabled=False),
                whatsapp=WhatsAppConfig(output_file=str(whatsapp_file)),
                dedupe=DedupeConfig(enabled=True, state_file=str(state_file)),
                affiliate=AffiliateConfig(enabled=False),
            )

            summary = run_workflow(config, skip_telegram=True)

            self.assertEqual(summary.duplicates_skipped, 1)
            self.assertEqual(summary.accepted, 1)
            self.assertIn("New deal 50% off", whatsapp_file.read_text(encoding="utf-8"))
            self.assertNotIn("Old deal 40% off", whatsapp_file.read_text(encoding="utf-8"))

    def test_discover_cuelinks_items_from_offers_payload(self):
        payload = {
            "offers": [
                {
                    "title": "Flipkart 40% off",
                    "offer_url": "https://www.flipkart.com/deal",
                    "description": "Limited time",
                    "category": "Shopping",
                }
            ]
        }
        items = discover_cuelinks_items(payload)
        self.assertEqual(len(items), 1)

    def test_fetch_cuelinks_offers_from_local_json_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            offers_file = Path(tmp_dir) / "offers.json"
            offers_file.write_text(
                json.dumps(
                    {
                        "data": {
                            "offers": [
                                {
                                    "offer_title": "Ajio 50% off",
                                    "landing_url": "https://www.ajio.com/deal",
                                    "coupon_code": "AJIO50",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            feed = FeedConfig(
                name="cuelinks-offers",
                url=str(offers_file),
                type="cuelinks_offers",
                items_path="data.offers",
                api_token_env="TEST_CUELINKS_TOKEN",
                max_pages=1,
                per_page=50,
            )
            os.environ["TEST_CUELINKS_TOKEN"] = "test-token-123456789012345678901234"
            try:
                deals = fetch_cuelinks_offers_from_url(
                    feed,
                    str(offers_file),
                    {"Authorization": 'Token token="x"'},
                )
            finally:
                os.environ.pop("TEST_CUELINKS_TOKEN", None)

            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0].title, "Ajio 50% off")
            self.assertEqual(deals[0].coupon, "AJIO50")

    def test_run_workflow_writes_csv_and_whatsapp(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "deals.csv"
            whatsapp_file = Path(tmp_dir) / "whatsapp.txt"
            config = WorkflowConfig(
                feeds=[
                    FeedConfig(
                        name="manual",
                        type="manual",
                        items=[
                            {
                                "title": "Test deal 30% off",
                                "url": "https://example.com/product",
                                "price": "700",
                                "original_price": "1000",
                            }
                        ],
                    )
                ],
                filters=FilterConfig(min_discount_percent=20, require_discount_data=True),
                telegram=TelegramConfig(enabled=False),
                whatsapp=WhatsAppConfig(output_file=str(whatsapp_file)),
                export_csv=ExportCsvConfig(enabled=True, output_file=str(csv_file)),
            )

            summary = run_workflow(config, skip_telegram=True)

            self.assertEqual(summary.accepted, 1)
            self.assertTrue(csv_file.exists())
            self.assertIn("Test deal 30% off", csv_file.read_text(encoding="utf-8"))
            self.assertEqual(summary.csv_file, str(csv_file))

    def test_csv_feed_accepts_title_case_headers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "sheet.csv"
            feed_file.write_text(
                "Title,URL,price,original_price,discount_percent\n"
                "Case Deal 40% off,https://example.com/case,600,1000,40\n",
                encoding="utf-8",
            )
            parsed = parse_feed(
                FeedConfig(name="google-sheet", url=str(feed_file), type="csv", currency="Rs. ")
            )
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].title, "Case Deal 40% off")

    def test_csv_feed_rejects_html_publish_mistake(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "bad.html"
            feed_file.write_text("<!DOCTYPE html><html><body>Sign in</body></html>", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_feed(FeedConfig(name="google-sheet", url=str(feed_file), type="csv"))

    def test_csv_feed_requires_data_rows(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "headers-only.csv"
            feed_file.write_text("title,url,price,original_price\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_feed(FeedConfig(name="google-sheet", url=str(feed_file), type="csv"))

    def test_main_fails_when_no_deals_accepted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = Path(tmp_dir) / "config.json"
            output_file = Path(tmp_dir) / "whatsapp.txt"
            config_file.write_text(
                json.dumps(
                    {
                        "feeds": [{"name": "manual", "type": "manual", "items": []}],
                        "affiliate": {"enabled": False},
                        "telegram": {"enabled": False},
                        "whatsapp": {"output_file": str(output_file)},
                    }
                ),
                encoding="utf-8",
            )
            exit_code = main(
                [
                    "--config",
                    str(config_file),
                    "--skip-telegram",
                    "--skip-affiliate",
                ]
            )
            self.assertEqual(exit_code, 1)

    def test_csv_feed_is_parsed_for_google_sheet_exports(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            feed_file = Path(tmp_dir) / "sheet.csv"
            feed_file.write_text(
                "title,url,price,original_price,coupon,category,description\n"
                "Sheet Deal 35% off,https://example.com/sheet,650,1000,SHEET35,Kitchen,Published from Google Sheets\n",
                encoding="utf-8",
            )

            parsed = parse_feed(
                FeedConfig(
                    name="google-sheet",
                    url=str(feed_file),
                    type="csv",
                    currency="Rs. ",
                )
            )

            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].title, "Sheet Deal 35% off")
            self.assertEqual(parsed[0].url, "https://example.com/sheet")
            self.assertEqual(parsed[0].price, 650)
            self.assertEqual(parsed[0].original_price, 1000)
            self.assertEqual(parsed[0].discount_percent, 35)


    def test_manual_feed_with_cuelinks_wraps_urls(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = Path(tmp_dir) / "whatsapp.txt"
            config = WorkflowConfig(
                feeds=[
                    FeedConfig(
                        name="manual",
                        type="manual",
                        items=[
                            {
                                "title": "Shoes 50% off",
                                "url": "https://www.ajio.com/shoes/p/123",
                                "price": "999",
                                "original_price": "1998",
                                "category": "Fashion",
                            }
                        ],
                        currency="Rs. ",
                    )
                ],
                filters=FilterConfig(min_discount_percent=25, require_discount_data=True),
                affiliate=AffiliateConfig(
                    enabled=True,
                    network="cuelinks",
                    channel_id="7102",
                    required=True,
                ),
                telegram=TelegramConfig(enabled=False),
                whatsapp=WhatsAppConfig(output_file=str(output_file)),
            )

            summary = run_workflow(config, skip_telegram=True)
            output = output_file.read_text(encoding="utf-8")

            self.assertEqual(summary.fetched, 1)
            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.errors, [])
            self.assertIn("https://linksredirect.com/?", output)
            self.assertIn("cid=7102", output)
            self.assertIn("url=https%3A%2F%2Fwww.ajio.com%2Fshoes%2Fp%2F123", output)

    def test_build_cuelinks_url_does_not_double_wrap(self):
        wrapped = build_cuelinks_url("https://www.tatacliq.com/product", "7102")
        self.assertEqual(
            build_cuelinks_url(wrapped, "7102"),
            wrapped,
        )


    def test_skip_affiliate_leaves_urls_unwrapped_even_when_required(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = Path(tmp_dir) / "whatsapp.txt"
            config = WorkflowConfig(
                feeds=[
                    FeedConfig(
                        name="manual",
                        type="manual",
                        items=[
                            {
                                "title": "Telegram test deal 50% off",
                                "url": "https://www.flipkart.com/test-product",
                                "price": "500",
                                "original_price": "1000",
                            }
                        ],
                    )
                ],
                filters=FilterConfig(min_discount_percent=25, require_discount_data=True),
                affiliate=AffiliateConfig(enabled=True, network="cuelinks", required=True),
                telegram=TelegramConfig(enabled=False),
                whatsapp=WhatsAppConfig(output_file=str(output_file)),
            )

            summary = run_workflow(config, skip_telegram=True, skip_affiliate=True)
            output = output_file.read_text(encoding="utf-8")

            self.assertEqual(summary.errors, [])
            self.assertIn("https://www.flipkart.com/test-product", output)
            self.assertNotIn("linksredirect.com", output)


    def test_run_workflow_skips_unconfigured_feed_urls(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = Path(tmp_dir) / "whatsapp.txt"
            config = WorkflowConfig(
                feeds=[FeedConfig(name="missing-feed", url="")],
                telegram=TelegramConfig(enabled=False),
                whatsapp=WhatsAppConfig(output_file=str(output_file)),
            )

            summary = run_workflow(config, skip_telegram=True)

            self.assertEqual(summary.fetched, 0)
            self.assertEqual(summary.accepted, 0)
            self.assertEqual(summary.skipped_feeds, ["missing-feed: missing feed URL"])
            self.assertTrue(output_file.exists())

    def test_load_config_expands_env_placeholders_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = Path(tmp_dir) / "deals.json"
            config_file.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "name": "env-feed",
                                "url": "${TEST_DEALS_FEED_URL:-https://fallback.example/feed.json}",
                                "headers": {
                                    "Authorization": "${TEST_DEALS_AUTH_HEADER:-}",
                                    "x-api-key": "${TEST_DEALS_API_KEY:-abc123}",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.environ["TEST_DEALS_FEED_URL"] = "https://partner.example/feed.json"
            try:
                config = load_config(config_file)
            finally:
                os.environ.pop("TEST_DEALS_FEED_URL", None)

            self.assertEqual(config.feeds[0].url, "https://partner.example/feed.json")
            self.assertEqual(config.feeds[0].headers["Authorization"], "")
            self.assertEqual(config.feeds[0].headers["x-api-key"], "abc123")



if __name__ == "__main__":
    unittest.main()
