import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from daily_delivery_guard import inspect_daily_report
from test_validate_daily_report import VALID_REPORT


class DailyDeliveryGuardTests(unittest.TestCase):
    def test_valid_report_is_validated_once_and_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "lee-daily-report.md"
            report.write_text(VALID_REPORT, encoding="utf-8")

            result = inspect_daily_report(report)

            self.assertTrue(result["already_delivered"])
            self.assertEqual(result["reason"], "valid_review_report_sealed")
            self.assertFalse(result["validation_skipped"])
            self.assertTrue(
                (report.parent / "lee-daily-delivery-seal.json").is_file()
            )

    def test_matching_seal_skips_repeat_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "lee-daily-report.md"
            report.write_text(VALID_REPORT, encoding="utf-8")
            inspect_daily_report(report)

            def fail_if_called(_markdown):
                raise AssertionError("validator must not run for a matching seal")

            result = inspect_daily_report(report, validate_fn=fail_if_called)

        self.assertTrue(result["already_delivered"])
        self.assertEqual(result["reason"], "validated_delivery_seal_matches")
        self.assertTrue(result["validation_skipped"])

    def test_changed_report_invalidates_seal_and_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "lee-daily-report.md"
            report.write_text(VALID_REPORT, encoding="utf-8")
            inspect_daily_report(report)
            report.write_text(
                VALID_REPORT.replace("## 选题雷达", "## 缺失选题雷达"),
                encoding="utf-8",
            )

            result = inspect_daily_report(report)

            self.assertFalse(result["already_delivered"])
            self.assertEqual(result["reason"], "report_invalid")
            self.assertFalse(result["validation_skipped"])
            self.assertFalse(
                (report.parent / "lee-daily-delivery-seal.json").exists()
            )

    def test_does_not_short_circuit_when_report_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "lee-daily-report.md"

            result = inspect_daily_report(report)

        self.assertFalse(result["already_delivered"])
        self.assertEqual(result["reason"], "report_missing")
        self.assertTrue(result["validation_skipped"])

    def test_does_not_short_circuit_when_status_is_not_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "lee-daily-report.md"
            report.write_text(
                VALID_REPORT.replace(
                    "状态：待 Lee 审核；未自动发布。",
                    "状态：待 Lee 审核。",
                ),
                encoding="utf-8",
            )

            result = inspect_daily_report(report)

        self.assertFalse(result["already_delivered"])
        self.assertEqual(result["reason"], "status_mismatch")
        self.assertFalse(result["validation_skipped"])

    def test_does_not_short_circuit_when_report_fails_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pathlib.Path(tmp) / "lee-daily-report.md"
            report.write_text(
                VALID_REPORT.replace("## 选题雷达", "## 缺失选题雷达"),
                encoding="utf-8",
            )

            result = inspect_daily_report(report)

        self.assertFalse(result["already_delivered"])
        self.assertEqual(result["reason"], "report_invalid")
        self.assertFalse(result["validation_skipped"])
        self.assertTrue(result["validation_errors"])


if __name__ == "__main__":
    unittest.main()
