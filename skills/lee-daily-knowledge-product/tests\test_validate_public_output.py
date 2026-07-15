import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_public_output import validate_public_text


class PublicOutputTests(unittest.TestCase):
    def test_rejects_real_brand_name(self):
        self.assertIn(
            "公开稿出现禁止披露的品牌名：韵糖居",
            validate_public_text("韵糖居准备扩张"),
        )

    def test_accepts_anonymous_brand_reference(self):
        self.assertEqual(
            validate_public_text("一家中小初创连锁品牌准备扩张"), []
        )


if __name__ == "__main__":
    unittest.main()
