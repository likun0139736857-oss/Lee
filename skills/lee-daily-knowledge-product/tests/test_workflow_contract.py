import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_radar_entrypoint_has_delivery_guard_before_retrieval(self):
        script = (ROOT / "scripts" / "run_daily_radar.ps1").read_text(
            encoding="utf-8"
        )
        guard_position = script.find("daily_delivery_guard.py")
        retrieval_position = script.find("& $accountRunner")

        self.assertGreaterEqual(guard_position, 0)
        self.assertGreater(retrieval_position, guard_position)
        self.assertIn("already_delivered", script)

    def test_wechat_guidance_routes_main_and_child_windows_by_executable_role(self):
        guidance = (
            ROOT / "references" / "wechat-account-retrieval.md"
        ).read_text(encoding="utf-8")

        self.assertIn(r"\Tencent\Weixin\Weixin.exe", guidance)
        self.assertIn("搜索前窗口基线", guidance)
        self.assertIn("不得把既有的无关页面", guidance)
        self.assertIn("新出现或内容发生变化", guidance)


if __name__ == "__main__":
    unittest.main()
