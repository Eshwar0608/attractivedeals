import json
import tempfile
import unittest
from pathlib import Path

from scripts.deals_channel import (
    FeedConfig,
    FilterConfig,
    TelegramConfig,
    WhatsAppConfig,
    WorkflowConfig,
    filter_deals,
    format_deal,
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


if __name__ == "__main__":
    unittest.main()
