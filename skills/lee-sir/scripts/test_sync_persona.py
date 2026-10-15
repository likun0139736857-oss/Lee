"""Isolated functional checks; no personal documents or network are used."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("sync_persona.py")
spec = importlib.util.spec_from_file_location("sync_persona", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncPersonaTests(unittest.TestCase):
    def setUp(self):
        # Keep test fixtures within the explicitly writable workspace on Windows.
        self.temporary = tempfile.TemporaryDirectory(prefix=".lee-sync-test-", dir=SCRIPT.parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source dir"
        self.skill = self.root / "skill"
        self.source.mkdir()
        (self.skill / "references").mkdir(parents=True)
        self.skill_file = self.skill / "SKILL.md"
        self.skill_file.write_text('---\nname: lee-sir\nmetadata:\n  version: "1.5.0"\n---\n\nUnrelated content.\n', encoding="utf-8")
        (self.skill / "references/decisions.md").write_text("# Existing events\n", encoding="utf-8")
        self.profile = self.source / sync.FILES[0][0]
        self.library = self.source / sync.FILES[1][0]
        self.profile.write_text("# Profile\n[decisions](./Lee个人决策库.md#test)\n[stats](./evidence stats.json)\n[web](https://example.test/page)\n", encoding="utf-8")
        self.library.write_text("# Decisions\n[profile](./lee-persona-profile-latest.md)\n[book]: <./book notes.md>\n", encoding="utf-8")

    def invoke(self, *args):
        process = subprocess.run([sys.executable, str(SCRIPT), "--source-dir", str(self.source), "--skill-dir", str(self.skill), *args], capture_output=True, text=True, encoding="utf-8")
        return process.returncode, json.loads(process.stdout)

    def snapshots(self):
        return {str(path.relative_to(self.skill)): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.skill.rglob("*") if path.is_file()}

    def test_initial_sync_links_and_private_manifest(self):
        code, result = self.invoke("--check")
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "needs_sync")
        self.assertFalse((self.skill / sync.STATE_NAME).exists())
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(result["first_registration"])
        self.assertEqual(result["skill_version"], "1.5.0")
        mirror = (self.skill / sync.FILES[0][1]).read_text(encoding="utf-8")
        self.assertIn("decision-library-current.md#test", mirror)
        self.assertIn("<" + (self.source / "evidence stats.json").as_posix() + ">", mirror)
        self.assertIn("https://example.test/page", mirror)
        self.assertIn("persona-current.md", (self.skill / sync.FILES[1][1]).read_text(encoding="utf-8"))
        state = json.loads((self.skill / sync.STATE_NAME).read_text(encoding="utf-8"))
        self.assertIs(state["private"], True)
        for item in state["files"]:
            self.assertEqual(item["source_sha256"], sync.digest(Path(item["source_path"]).read_bytes()))
            self.assertEqual(item["mirror_sha256"], sync.digest((self.skill / item["mirror_path"]).read_bytes()))
        self.assertNotIn("1.5.1", self.skill_file.read_text(encoding="utf-8"))

    def test_repeat_is_byte_and_mtime_idempotent(self):
        self.assertEqual(self.invoke()[0], 0)
        before = self.snapshots()
        self.assertEqual(self.invoke("--check")[0], 0)
        self.assertEqual(self.invoke()[1]["status"], "fresh")
        self.assertEqual(before, self.snapshots())

    def test_source_change_bumps_once_and_backs_up(self):
        self.assertEqual(self.invoke()[0], 0)
        previous_mirror = (self.skill / sync.FILES[0][1]).read_bytes()
        self.profile.write_text(self.profile.read_text(encoding="utf-8") + "\nNew confirmed principle.\n", encoding="utf-8")
        self.assertEqual(self.invoke("--check")[0], 2)
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(result["skill_version"], "1.5.1")
        backup = Path(result["backup_path"])
        self.assertEqual((backup / (sync.FILES[0][1] + ".bak")).read_bytes(), previous_mirror)
        self.assertTrue((backup / "SKILL.md.bak").is_file())
        event = (self.skill / "references/decisions.md").read_text(encoding="utf-8")
        self.assertIn("1.5.1", event)
        self.assertNotIn("New confirmed principle", event)
        self.assertEqual(self.invoke()[1]["status"], "fresh")
        self.assertFalse(any(path.name == "SKILL.md" for path in backup.rglob("*")))

    def test_missing_source_does_not_damage_mirrors(self):
        self.assertEqual(self.invoke()[0], 0)
        before = self.snapshots()
        self.library.unlink()
        for args in (("--check",), ()):
            code, result = self.invoke(*args)
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], "source_missing")
            self.assertEqual(before, self.snapshots())

    def test_tampered_mirror_detected_and_repaired(self):
        self.assertEqual(self.invoke()[0], 0)
        path = self.skill / sync.FILES[0][1]
        expected = path.read_bytes()
        path.write_text("Manual mirror edit.\n", encoding="utf-8")
        self.assertEqual(self.invoke("--check")[0], 2)
        self.assertEqual(self.invoke()[1]["skill_version"], "1.5.1")
        self.assertEqual(path.read_bytes(), expected)

    def test_failed_write_restores_targets(self):
        self.assertEqual(self.invoke()[0], 0)
        before = {key: value[0] for key, value in self.snapshots().items()}
        self.profile.write_text("# New profile\n", encoding="utf-8")
        self.library.write_text("# New library\n", encoding="utf-8")
        original_write = sync.atomic_write
        count = 0

        def fail_second(path, data):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("injected test failure")
            return original_write(path, data)

        sync.atomic_write = fail_second
        try:
            with self.assertRaises(sync.SyncError), redirect_stdout(io.StringIO()):
                sync.run(self.source, self.skill, False)
        finally:
            sync.atomic_write = original_write
        after = {str(path.relative_to(self.skill)): path.read_bytes() for path in self.skill.rglob("*") if path.is_file() and "history" not in path.relative_to(self.skill).parts}
        self.assertEqual(before, after)

    def test_empty_or_invalid_utf8_source_is_rejected(self):
        self.assertEqual(self.invoke()[0], 0)
        before = self.snapshots()
        for content in (b"  \n", b"\xff\xfe"):
            self.library.write_bytes(content)
            code, result = self.invoke()
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "error")
            self.assertEqual(before, self.snapshots())

    def test_concurrent_source_edit_is_preserved_and_mirrors_restored(self):
        self.assertEqual(self.invoke()[0], 0)
        before = {key: value[0] for key, value in self.snapshots().items()}
        self.profile.write_text("# Update pending\n", encoding="utf-8")
        original_write = sync.atomic_write
        count = 0

        def change_source_after_first_write(path, data):
            nonlocal count
            count += 1
            result = original_write(path, data)
            if count == 1:
                self.profile.write_text("# Concurrent source edit\n", encoding="utf-8")
            return result

        sync.atomic_write = change_source_after_first_write
        try:
            with self.assertRaises(sync.SyncError):
                sync.run(self.source, self.skill, False)
        finally:
            sync.atomic_write = original_write
        after = {str(path.relative_to(self.skill)): path.read_bytes() for path in self.skill.rglob("*") if path.is_file() and "history" not in path.relative_to(self.skill).parts}
        self.assertEqual(before, after)
        self.assertEqual(self.profile.read_text(encoding="utf-8"), "# Concurrent source edit\n")

    def test_concurrent_target_edit_is_not_rolled_back(self):
        self.assertEqual(self.invoke()[0], 0)
        self.profile.write_text("# Update pending\n", encoding="utf-8")
        mirror = self.skill / sync.FILES[0][1]
        original_write = sync.atomic_write
        count = 0

        def change_target_after_first_write(path, data):
            nonlocal count
            count += 1
            result = original_write(path, data)
            if count == 1:
                mirror.write_text("# Concurrent target edit\n", encoding="utf-8")
            return result

        sync.atomic_write = change_target_after_first_write
        try:
            with self.assertRaisesRegex(sync.SyncError, "concurrent target edits preserved"):
                sync.run(self.source, self.skill, False)
        finally:
            sync.atomic_write = original_write
        self.assertEqual(mirror.read_text(encoding="utf-8"), "# Concurrent target edit\n")


if __name__ == "__main__":
    unittest.main(verbosity=1)
