#!/usr/bin/env python3
"""Mirror the two authoritative local persona documents; never upload them.

Only Python's standard library is required.  Exit codes: 0 = fresh/synced,
2 = source missing or --check reports stale, 1 = unsafe input/operation failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote
from uuid import uuid4


DEFAULT_SOURCE_DIR = Path("D:/BaiduSyncdisk/raw/01 连锁智库")
FILES = (
    ("lee-persona-profile-latest.md", "references/persona-current.md"),
    ("Lee个人决策库.md", "references/decision-library-current.md"),
)
STATE_NAME = "references/persona-sync.json"
SCHEMA_VERSION = 1
INLINE_LINK = re.compile(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^)\n]*)(\))")
REFERENCE_LINK = re.compile(r"(?m)^( {0,3}\[[^\]\n]+\]:[ \t]*)(<[^>\n]+>|\S+)([^\n]*)$")


class SyncError(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def report(value: dict) -> None:
    # ASCII JSON also works in legacy Windows consoles without changing codecs.
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def read_optional(path: Path) -> bytes | None:
    if path.is_symlink():
        raise SyncError("Refusing a symlink target: " + str(path))
    return path.read_bytes() if path.exists() else None


def guarded_path(skill_dir: Path, relative: str) -> Path:
    path = skill_dir / relative
    try:
        path.resolve().relative_to(skill_dir.resolve())
    except ValueError as exc:
        raise SyncError("Target escapes the skill directory: " + relative) from exc
    return path


def destination_for_link(raw: str, source_dir: Path) -> str:
    """Rewrite local destinations, preserving an optional Markdown title."""
    value = raw.strip()
    if not value:
        return raw
    if value.startswith("<"):
        end = value.find(">")
        if end < 0:
            return raw
        destination, suffix = value[1:end], value[end + 1 :]
    else:
        # An optional quoted title is separate from the destination.
        title = re.search(r'\s+(?:"[^"\n]*"|\x27[^\x27\n]*\x27)\s*$', value)
        destination = value[: title.start()] if title else value
        suffix = value[title.start() :] if title else ""
    if destination.startswith(("#", "//")):
        return raw
    # Keep web/mail links.  Windows drive letters are local file paths.
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", destination) and not re.match(r"^[A-Za-z]:[\\/]", destination):
        return raw
    local, separator, fragment = destination.partition("#")
    decoded = unquote(local).replace("\\", "/")
    resolved = (source_dir / decoded).resolve()
    for source_name, mirror_name in FILES:
        if resolved == (source_dir / source_name).resolve():
            target = Path(mirror_name).name
            return target + (separator + fragment if separator else "") + suffix
    if Path(decoded).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", decoded):
        return raw
    target = resolved.as_posix() + (separator + fragment if separator else "")
    return "<" + target + ">" + suffix


def render_mirror(text: str, source_path: Path, source_dir: Path) -> bytes:
    def inline(match: re.Match) -> str:
        return match.group(1) + destination_for_link(match.group(2), source_dir) + match.group(3)

    def reference(match: re.Match) -> str:
        return match.group(1) + destination_for_link(match.group(2), source_dir) + match.group(3)

    body = INLINE_LINK.sub(inline, text)
    body = REFERENCE_LINK.sub(reference, body)
    header = (
        "<!-- lee-sir: generated private local mirror; do not edit -->\n"
        "> **自动生成的本地私密只读镜像，不得单独编辑。**\n"
        "> 唯一编辑源：`" + str(source_path) + "`。请修改源文档后运行同步器。\n"
        "> 本次同步仅搬运既有内容，不新增原书核验、实践有效或案例因果已验证的结论。\n\n"
    )
    return (header + body).encode("utf-8")


def bump_version(data: bytes) -> tuple[bytes, str]:
    text = data.decode("utf-8")
    front = re.match(r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not front:
        raise SyncError("SKILL.md has no YAML front matter")
    body = front.group("body")
    metadata = re.search(r"(?m)^metadata:\s*\r?\n(?P<fields>(?:[ \t]+[^\n]*(?:\n|$))*)", body)
    if not metadata:
        raise SyncError("SKILL.md metadata is missing")
    version = re.search(r"(?m)^(?P<prefix>[ \t]+version:[ \t]*[\"']?)(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<suffix>[\"']?[ \t]*\r?)$", metadata.group("fields"))
    if not version:
        raise SyncError("SKILL.md metadata.version must be major.minor.patch")
    new_version = version.group("major") + "." + version.group("minor") + "." + str(int(version.group("patch")) + 1)
    start = front.start("body") + metadata.start("fields") + version.start("major")
    end = front.start("body") + metadata.start("fields") + version.end("patch")
    return (text[:start] + new_version + text[end:]).encode("utf-8"), new_version


def current_version(data: bytes) -> str:
    text = data.decode("utf-8")
    match = re.search(r"(?m)^[ \t]+version:[ \t]*[\"']?(\d+\.\d+\.\d+)", text)
    if not match:
        raise SyncError("SKILL.md metadata.version is missing")
    return match.group(1)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".persona-sync-", suffix=".tmp", dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def assert_unchanged(sources: dict[Path, bytes], targets: dict[Path, bytes | None]) -> None:
    for path, expected in sources.items():
        if not path.is_file() or digest(path.read_bytes()) != digest(expected):
            raise SyncError("A source changed while preparing; retry without overwriting it: " + str(path))
    for path, expected in targets.items():
        if read_optional(path) != expected:
            raise SyncError("A target changed while preparing; retry without overwriting it: " + str(path))


def commit(skill_dir: Path, sources: dict[Path, bytes], originals: dict[Path, bytes | None], updates: dict[Path, bytes]) -> str | None:
    assert_unchanged(sources, originals)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    backups = guarded_path(skill_dir, "history/" + stamp)
    backed_up = False
    for path in updates:
        previous = originals[path]
        if previous is not None:
            backup = backups / (path.relative_to(skill_dir).as_posix() + ".bak")
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as stream:
                stream.write(previous)
            backed_up = True
    # Recheck after backups, immediately before making any changes.
    assert_unchanged(sources, originals)
    applied: list[Path] = []
    try:
        for path, data in updates.items():
            for source_path, expected in sources.items():
                if not source_path.is_file() or digest(source_path.read_bytes()) != digest(expected):
                    raise SyncError("A source changed during commit: " + str(source_path))
            if read_optional(path) != originals[path]:
                raise SyncError("A target changed during commit: " + str(path))
            atomic_write(path, data)
            applied.append(path)
        for source_path, expected in sources.items():
            if not source_path.is_file() or digest(source_path.read_bytes()) != digest(expected):
                raise SyncError("A source changed during commit: " + str(source_path))
        for path, data in updates.items():
            if read_optional(path) != data:
                raise SyncError("A target changed after commit: " + str(path))
    except Exception as exc:
        conflicts = []
        for path in reversed(applied):
            # Never roll back a concurrent edit that replaced our own bytes.
            if read_optional(path) != updates[path]:
                conflicts.append(str(path))
                continue
            previous = originals[path]
            if previous is None:
                path.unlink()
            else:
                atomic_write(path, previous)
        if conflicts:
            raise SyncError("Sync failed; concurrent target edits preserved; inspect backups: " + ", ".join(conflicts)) from exc
        raise SyncError("Sync failed; previous target files restored: " + str(exc)) from exc
    return str(backups) if backed_up else None


def run(source_dir: Path, skill_dir: Path, check: bool) -> int:
    source_dir, skill_dir = source_dir.expanduser().resolve(), skill_dir.expanduser().resolve()
    missing = [name for name, _ in FILES if not (source_dir / name).is_file()]
    if missing:
        report({"status": "needs_sync", "reason": "source_missing", "missing_sources": missing, "changed": False, "private": True})
        return 2
    sources: dict[Path, bytes] = {}
    entries = []
    expected_mirrors: dict[Path, bytes] = {}
    reasons = []
    for source_name, mirror_name in FILES:
        source_path = source_dir / source_name
        raw = source_path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SyncError("Source is not UTF-8: " + source_name) from exc
        if not text.strip():
            raise SyncError("Source is empty: " + source_name)
        if source_path.read_bytes() != raw:
            raise SyncError("Source changed while reading: " + source_name)
        sources[source_path] = raw
        mirror_path = guarded_path(skill_dir, mirror_name)
        if mirror_path in sources or mirror_path == source_path:
            raise SyncError("Source and mirror must be distinct")
        rendered = render_mirror(text, source_path, source_dir)
        expected_mirrors[mirror_path] = rendered
        entries.append({
            "source_name": source_name,
            "source_path": str(source_path),
            "source_sha256": digest(raw),
            "source_modified_at": datetime.fromtimestamp(source_path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "mirror_path": mirror_name,
            "mirror_sha256": digest(rendered),
            "private": True,
        })
    if set(sources) & set(expected_mirrors):
        raise SyncError("Source and mirror must be distinct")
    state_path = guarded_path(skill_dir, STATE_NAME)
    original_state = read_optional(state_path)
    state = None
    if original_state is not None:
        try:
            state = json.loads(original_state.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            reasons.append("manifest_invalid")
        if not isinstance(state, dict):
            state = None
            if "manifest_invalid" not in reasons:
                reasons.append("manifest_invalid")
    else:
        reasons.append("manifest_missing")
    originals = {path: read_optional(path) for path in expected_mirrors}
    originals[state_path] = original_state
    for path, expected in expected_mirrors.items():
        if originals[path] != expected:
            reasons.append("mirror_missing_or_changed:" + path.name)
    # Source mtime alone is not content change; do not create no-op versions.
    fields = ("source_name", "source_path", "source_sha256", "mirror_path", "mirror_sha256", "private")
    signature = lambda items: [{key: item.get(key) for key in fields} for item in items]
    if state is not None:
        previous_files = state.get("files")
        if not isinstance(previous_files, list) or any(not isinstance(item, dict) for item in previous_files):
            reasons.append("manifest_invalid")
        elif signature(previous_files) != signature(entries):
            reasons.append("sources_or_manifest_changed")
        if state.get("schema_version") != SCHEMA_VERSION or state.get("private") is not True:
            reasons.append("manifest_schema_changed")
    assert_unchanged(sources, originals)
    if not reasons:
        report({"status": "fresh", "changed": False, "source_count": len(FILES), "private": True})
        return 0
    if check:
        report({"status": "needs_sync", "changed": False, "source_count": len(FILES), "reasons": reasons, "private": True})
        return 2
    if "manifest_invalid" in reasons:
        raise SyncError("Invalid existing sync manifest; preserve and inspect it before syncing")
    skill_path = guarded_path(skill_dir, "SKILL.md")
    skill_bytes = read_optional(skill_path)
    if skill_bytes is None:
        raise SyncError("SKILL.md is missing")
    originals[skill_path] = skill_bytes
    version = current_version(skill_bytes)
    updates = {path: data for path, data in expected_mirrors.items() if originals[path] != data}
    first_registration = original_state is None
    generated_at = utc_now()
    if not first_registration:
        bumped, version = bump_version(skill_bytes)
        updates[skill_path] = bumped
        decisions_path = guarded_path(skill_dir, "references/decisions.md")
        old_decisions = read_optional(decisions_path)
        originals[decisions_path] = old_decisions
        decisions = (old_decisions or b"").decode("utf-8")
        event = (
            "\n\n## PERSONA-SYNC " + generated_at + " | " + version + "\n\n"
            "- 同步读取范围：两份唯一编辑源全文及对应本地镜像；仅搬运，不新增内容验证结论。\n"
            + "".join("- " + entry["mirror_path"] + "：源SHA256=" + entry["source_sha256"] + "；镜像SHA256=" + entry["mirror_sha256"] + "。\n" for entry in entries)
        )
        updates[decisions_path] = (decisions.rstrip() + event).encode("utf-8")
    next_state = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "private": True,
        "scope": "local_only_no_upload_no_new_evidence_verification",
        "source_dir": str(source_dir),
        "skill_version": version,
        "files": entries,
    }
    updates[state_path] = (json.dumps(next_state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    backup_path = commit(skill_dir, sources, originals, updates)
    report({"status": "synced", "changed": True, "source_count": len(FILES), "first_registration": first_registration, "skill_version": version, "changed_file_count": len(updates), "backup_path": backup_path, "private": True})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="Read-only: return 0 when fresh or 2 when stale/source missing")
    args = parser.parse_args()
    try:
        return run(args.source_dir, args.skill_dir, args.check)
    except (SyncError, OSError, UnicodeError, ValueError) as exc:
        report({"status": "error", "changed": False, "reason": str(exc), "private": True})
        return 1


if __name__ == "__main__":
    sys.exit(main())
