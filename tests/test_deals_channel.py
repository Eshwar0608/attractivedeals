import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.deals_channel import (
    AffiliateConfig,
    FeedConfig,
    FilterConfig,
    TelegramConfig,
    WhatsAppConfig,
    WorkflowConfig,
    build_cuelinks_url,
    filter_deals,
    load_config,
    format_deal,
    main,
    parse_feed,
    run_workflow,
)


class DealsChannelTests(unittest.TestCase):
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
            accepted = filter_deals(
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

            message = format_deal(deal, ["#Deals", "Top Picks"])

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
