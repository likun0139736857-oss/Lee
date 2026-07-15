import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_daily_report import validate_report


VALID_REPORT = """# Lee连锁智库日报
## 创作入口
Lee 投喂观点。原始输入边界：仅作为作者意图，外部事实另行核验。
## 数据覆盖说明
检索窗口：2026-07-10 至 2026-07-13。
## 选题雷达
1. 候选选题一
2. 候选选题二
3. 候选选题三
4. 候选选题四
5. 候选选题五
## 今日推荐题
推荐：加盟商现金流比开店数更早暴露风险。
## 今日待审稿
### 标题
开店数增长，为什么加盟商先感到压力？
### 正文
公开报道显示门店扩张与加盟商现金流需要分开看。
## 证据审核台账（仅内部审核，不进入成稿）
| 类型 | 主张 | 来源或依据 | 核验状态 |
| --- | --- | --- | --- |
| 事实 | 门店经营信号 | https://example.test/source | 已核验 |
| 推断 | 总部应增加现金流预警 | 基于上述事实的经营判断 | 待验证 |
## 编辑附注
状态：待 Lee 审核；未自动发布。
"""


class DailyReportTests(unittest.TestCase):
    def test_accepts_complete_review_only_report(self):
        self.assertEqual(validate_report(VALID_REPORT), [])

    def test_rejects_report_without_evidence_register(self):
        errors = validate_report(
            VALID_REPORT.replace("## 证据审核台账（仅内部审核，不进入成稿）", "## 来源")
        )
        self.assertIn("缺少证据审核台账（仅内部审核，不进入成稿）", errors)

    def test_rejects_evidence_labels_in_article_draft(self):
        errors = validate_report(
            VALID_REPORT.replace("公开报道显示门店扩张与加盟商现金流需要分开看。", "[事实] 公开报道显示门店扩张与加盟商现金流需要分开看。")
        )
        self.assertIn("成稿正文不应显示证据标签", errors)

    def test_rejects_claim_of_auto_publication(self):
        errors = validate_report(
            VALID_REPORT.replace("未自动发布", "已自动发布到公众号")
        )
        self.assertIn("出现禁止的自动发布表述", errors)

    def test_rejects_real_brand_name_in_article_draft(self):
        errors = validate_report(
            VALID_REPORT.replace(
                "公开报道显示门店扩张与加盟商现金流需要分开看。",
                "韵糖居的门店扩张与加盟商现金流需要分开看。",
            )
        )
        self.assertIn("公开稿出现禁止披露的品牌名：韵糖居", errors)

    def test_allows_internal_brand_mapping_in_evidence_register(self):
        report = VALID_REPORT.replace(
            "| 事实 | 门店经营信号 |",
            "| 内部素材 | 韵糖居对应中小初创连锁品牌 |",
        )
        self.assertEqual(validate_report(report), [])


if __name__ == "__main__":
    unittest.main()

