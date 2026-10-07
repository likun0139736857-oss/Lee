import pathlib
import sys
import unittest
from datetime import date
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wechat_account_retrieval import (
    SogouClient,
    parse_feed,
    parse_sogou_results,
    retrieve_accounts,
)


class WeChatAccountRetrievalTests(unittest.TestCase):
    def test_parses_exact_sogou_author_and_publish_time(self):
        raw = """
        <html><body><ul class="news-list"><li>
          <h3><a href="/link?url=abc">餐饮连锁增长复盘</a></h3>
          <p class="txt-info">门店与供应链案例</p>
          <div class="s-p">
            <span class="all-time-y2">红餐网</span>
            <script>document.write(timeConvert('1784217600'));</script>
          </div>
        </li></ul></body></html>
        """

        items = parse_sogou_results(raw, "红餐网", ["红餐网"])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["account_name"], "红餐网")
        self.assertTrue(items[0]["identity_match"])
        self.assertTrue(items[0]["published_at"])
        self.assertEqual(items[0]["coverage_level"], "partial")

    def test_parses_rss_as_confirmed_account_feed(self):
        raw = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Chain case</title>
          <link>https://example.test/article</link>
          <author>Account A</author>
          <pubDate>Fri, 17 Jul 2026 08:00:00 +0800</pubDate>
          <description><![CDATA[<p>Full body</p>]]></description>
        </item></channel></rss>"""

        items = parse_feed(raw, "https://example.test/feed.rss")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_backend"], "rss")
        self.assertEqual(items[0]["coverage_level"], "confirmed")
        self.assertTrue(items[0]["content_available"])

    @patch.object(SogouClient, "_open")
    def test_sogou_resolver_preserves_timestamp_parameter(self, open_page):
        open_page.return_value = (
            "<script>url += 'https://mp.weixin.qq.com/s?__biz=test"
            "&timestamp=123';</script>",
            "https://weixin.sogou.com/link?url=test",
        )

        resolved = SogouClient(delay_seconds=0).resolve_result(
            "https://weixin.sogou.com/link?url=test",
            "https://weixin.sogou.com/weixin?type=2&query=test",
        )

        self.assertIn("&timestamp=123", resolved)
        self.assertNotIn("×", resolved)

    @patch("wechat_account_retrieval.SogouClient.search")
    def test_sogou_success_is_partial_not_confirmed(self, search):
        search.return_value = [
            {
                "title": "餐饮连锁增长复盘",
                "summary": "门店案例",
                "account_name": "红餐网",
                "published_at": "2026-07-17T08:00:00+08:00",
                "sogou_result_url": "",
                "search_query": "红餐网",
                "identity_match": True,
                "source_backend": "sogou_wechat",
                "coverage_level": "partial",
                "verification_required": True,
                "link_stability": "ephemeral",
            }
        ]
        source_pool = {
            "priority_accounts": [
                {
                    "canonical_name": "红餐网",
                    "aliases": ["红餐网"],
                    "focus": ["餐饮"],
                    "feed_urls": [],
                }
            ]
        }

        result = retrieve_accounts(
            source_pool,
            date(2026, 7, 17),
            read_limit_per_account=0,
            delay_seconds=0,
        )

        self.assertEqual(result["coverage"]["confirmed"], [])
        self.assertEqual(result["coverage"]["partial"], ["红餐网"])
        self.assertEqual(result["coverage"]["matched"], ["红餐网"])

    @patch("wechat_account_retrieval.SogouClient.search")
    def test_stale_sogou_index_requires_wechat_verification(self, search):
        search.return_value = [
            {
                "title": "旧索引文章",
                "summary": "旧内容",
                "account_name": "红餐网",
                "published_at": "2024-02-23T08:00:00+08:00",
                "sogou_result_url": "",
                "search_query": "红餐网",
                "identity_match": True,
                "source_backend": "sogou_wechat",
                "coverage_level": "partial",
                "verification_required": True,
                "link_stability": "ephemeral",
            }
        ]
        source_pool = {
            "priority_accounts": [
                {
                    "canonical_name": "红餐网",
                    "aliases": ["红餐网"],
                    "focus": ["餐饮"],
                    "feed_urls": [],
                }
            ]
        }

        result = retrieve_accounts(
            source_pool,
            date(2026, 7, 24),
            read_limit_per_account=0,
            delay_seconds=0,
        )

        account = result["accounts"][0]
        backend = account["backend_runs"][0]
        self.assertEqual(account["coverage_status"], "partial")
        self.assertEqual(account["index_freshness"], "stale")
        self.assertEqual(
            account["latest_indexed_at"], "2024-02-23T08:00:00+08:00"
        )
        self.assertTrue(account["requires_wechat_verification"])
        self.assertIn("does not verify current WeChat updates", account["coverage_reason"])
        self.assertEqual(backend["index_freshness"], "stale")
        self.assertEqual(backend["latest_exact_author_result_at"], account["latest_indexed_at"])
        self.assertEqual(account["articles"], [])

    @patch("wechat_account_retrieval.SogouClient.search")
    def test_verified_wechat_history_is_confirmed(self, search):
        source_pool = {
            "priority_accounts": [
                {
                    "canonical_name": "红餐网",
                    "aliases": ["红餐网"],
                    "focus": ["餐饮"],
                    "feed_urls": [],
                }
            ]
        }
        verified = {
            "红餐网": {
                "account_identity_verified": True,
                "history_page_checked": True,
                "checked_at": "2026-07-17T17:00:00+08:00",
                "articles": [
                    {
                        "title": "山东老牌酒楼正式闭店",
                        "account_name": "红餐网",
                        "published_at": "2026-07-16T19:30:00+08:00",
                        "body_text": "正文",
                    }
                ],
            }
        }

        result = retrieve_accounts(
            source_pool,
            date(2026, 7, 17),
            read_limit_per_account=0,
            delay_seconds=0,
            verified_accounts=verified,
        )

        self.assertEqual(result["coverage"]["confirmed"], ["红餐网"])
        self.assertEqual(result["coverage"]["partial"], [])
        self.assertEqual(result["coverage"]["current_matched"], ["红餐网"])
        self.assertEqual(
            result["accounts"][0]["articles"][0]["source_backend"],
            "wechat_desktop",
        )
        search.assert_not_called()

    @patch("wechat_account_retrieval.SogouClient.search")
    def test_authenticated_wechat_search_is_ingested_as_partial(self, search):
        source_pool = {
            "priority_accounts": [
                {
                    "canonical_name": "红餐网",
                    "aliases": ["红餐网"],
                    "focus": ["餐饮"],
                    "feed_urls": [],
                }
            ]
        }
        verified = {
            "红餐网": {
                "account_identity_verified": True,
                "platform_search_checked": True,
                "history_page_checked": False,
                "checked_at": "2026-07-24T10:20:00+08:00",
                "articles": [
                    {
                        "title": "单价1.9元起！生鲜巨头朴朴开了家2000平的火锅店",
                        "account_name": "红餐网",
                        "published_at": "2026-07-23T20:00:00+08:00",
                    }
                ],
            }
        }

        result = retrieve_accounts(
            source_pool,
            date(2026, 7, 24),
            read_limit_per_account=0,
            delay_seconds=0,
            verified_accounts=verified,
        )

        account = result["accounts"][0]
        self.assertEqual(account["coverage_status"], "partial")
        self.assertTrue(account["requires_wechat_verification"])
        self.assertEqual(account["current_hits"], 1)
        self.assertEqual(
            account["articles"][0]["source_backend"],
            "wechat_authenticated_search",
        )
        self.assertEqual(
            account["backend_runs"][0]["backend"],
            "wechat_authenticated_search",
        )
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
