import json
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_topic_radar import prepare_topic_radar


class TopicRadarTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(
            (ROOT / "config" / "topic-clusters.json").read_text(encoding="utf-8")
        )
        self.source_pool = json.loads(
            (ROOT / "config" / "editorial-source-pool.json").read_text(
                encoding="utf-8"
            )
        )

    def test_marks_sports_joining_as_excluded(self):
        data = {
            "sectors": [
                {
                    "name": "加盟治理",
                    "items": [
                        {
                            "title": "国安加盟两赛季仅踢一场",
                            "matchedKeyword": "加盟",
                            "publicTime": "2026-07-11 10:55:01",
                            "oriUrl": "https://example.test/sports",
                        }
                    ],
                }
            ]
        }

        item = prepare_topic_radar(
            data, self.config, self.source_pool, "2026-07-15"
        )["candidates"][0]

        self.assertEqual(item["relevance_status"], "excluded")
        self.assertIn("体育", item["relevance_reasons"])

    def test_priority_source_pool_contains_approved_accounts(self):
        source_pool_path = ROOT / "config" / "editorial-source-pool.json"
        self.assertTrue(source_pool_path.exists(), "缺少重点公众号配置")
        source_pool = json.loads(source_pool_path.read_text(encoding="utf-8"))
        names = {
            item["canonical_name"] for item in source_pool["priority_accounts"]
        }
        self.assertTrue(
            {
                "红餐网",
                "笔记侠",
                "餐企老板内参",
                "咖门",
                "红餐智库",
                "混沌学园",
                "刘润",
                "L先生说",
                "晚点LatePost",
                "增长研习社",
            }
            <= names
        )
        self.assertEqual(source_pool["candidate_cap_per_account"], 2)

    def test_keeps_chain_business_item_with_source_fields(self):
        data = {
            "sectors": [
                {
                    "name": "加盟治理",
                    "items": [
                        {
                            "title": "库迪闭店与加盟商现金流压力",
                            "matchedKeyword": "加盟商",
                            "publicTime": "2026-07-11 18:51:32",
                            "oriUrl": "https://example.test/chain",
                        }
                    ],
                }
            ]
        }

        item = prepare_topic_radar(
            data, self.config, self.source_pool, "2026-07-15"
        )["candidates"][0]

        self.assertEqual(item["relevance_status"], "candidate")
        self.assertEqual(item["source_url"], "https://example.test/chain")
        self.assertEqual(item["published_at"], "2026-07-11 18:51:32")

    def test_matches_priority_account_and_marks_enterprise_case(self):
        data = {
            "sectors": [
                {
                    "name": "企业案例",
                    "items": [
                        {
                            "title": "餐饮连锁品牌的现金流复盘",
                            "summary": "一家企业的转型案例",
                            "accountName": "餐企内参",
                            "matchedKeyword": "现金流",
                            "publicTime": "2026-07-14 10:00:00",
                            "noteLink": "https://example.test/case",
                        }
                    ],
                }
            ]
        }

        result = prepare_topic_radar(
            data, self.config, self.source_pool, "2026-07-15"
        )
        item = result["candidates"][0]

        self.assertEqual(item["account_name"], "餐企内参")
        self.assertEqual(item["priority_account"], "餐企老板内参")
        self.assertEqual(item["source_role"], "topic_lead")
        self.assertEqual(item["topic_type"], "enterprise_case")
        self.assertEqual(item["content_window"], "hot_3d")
        self.assertTrue(item["verification_required"])
        self.assertIn("餐企老板内参", result["source_coverage"]["matched"])

    def test_keeps_thirty_day_case_but_downgrades_old_general_signal(self):
        data = {
            "sectors": [
                {
                    "name": "企业案例",
                    "items": [
                        {
                            "title": "零售连锁转型复盘",
                            "summary": "企业案例与商业模式变化",
                            "accountName": "晚点LatePost",
                            "publicTime": "2026-06-25 10:00:00",
                            "noteLink": "https://example.test/old-case",
                        },
                        {
                            "title": "连锁门店经营观察",
                            "summary": "门店经营的日常信号",
                            "accountName": "其他账号",
                            "publicTime": "2026-06-25 11:00:00",
                            "noteLink": "https://example.test/old-general",
                        },
                    ],
                }
            ]
        }

        result = prepare_topic_radar(
            data, self.config, self.source_pool, "2026-07-15"
        )["candidates"]

        self.assertEqual(result[0]["content_window"], "case_30d")
        self.assertEqual(result[0]["relevance_status"], "candidate")
        self.assertEqual(result[1]["content_window"], "background")
        self.assertEqual(result[1]["relevance_status"], "manual_review")

    def test_deduplicates_urls_and_caps_priority_account_candidates(self):
        items = []
        for index, url in enumerate(
            [
                "https://example.test/a",
                "https://example.test/b",
                "https://example.test/a",
                "https://example.test/c",
            ],
            start=1,
        ):
            items.append(
                {
                    "title": f"连锁门店增长案例 {index}",
                    "summary": "企业增长复盘",
                    "accountName": "红餐网",
                    "publicTime": "2026-07-14 10:00:00",
                    "noteLink": url,
                }
            )
        data = {"sectors": [{"name": "企业案例", "items": items}]}

        result = prepare_topic_radar(
            data, self.config, self.source_pool, "2026-07-15"
        )["candidates"]

        self.assertEqual(len(result), 3)
        self.assertEqual(
            [item["relevance_status"] for item in result],
            ["candidate", "candidate", "manual_review"],
        )
        self.assertTrue(
            any("每日候选上限" in reason for reason in result[2]["relevance_reasons"])
        )

    def test_reports_unmatched_priority_accounts_as_coverage_gaps(self):
        data = {
            "sectors": [
                {
                    "name": "组织与认知",
                    "items": [
                        {
                            "title": "长期主义与组织学习",
                            "summary": "管理者如何做决策",
                            "accountName": "L先生说",
                            "publicTime": "2026-07-15 08:00:00",
                            "noteLink": "https://example.test/learning",
                        }
                    ],
                }
            ]
        }

        coverage = prepare_topic_radar(
            data, self.config, self.source_pool, "2026-07-15"
        )["source_coverage"]

        self.assertEqual(coverage["matched"], ["L先生说"])
        self.assertIn("红餐网", coverage["missing"])
        self.assertNotIn("L先生说", coverage["missing"])


if __name__ == "__main__":
    unittest.main()

