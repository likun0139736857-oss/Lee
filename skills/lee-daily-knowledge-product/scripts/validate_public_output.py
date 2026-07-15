"""Reject private brand identifiers in public article artifacts."""

from __future__ import annotations

import pathlib
import sys


BANNED_PUBLIC_IDENTIFIERS = ("韵糖居",)


def validate_public_text(text: str) -> list[str]:
    return [
        f"公开稿出现禁止披露的品牌名：{identifier}"
        for identifier in BANNED_PUBLIC_IDENTIFIERS
        if identifier in text
    ]


def validate_paths(paths: list[pathlib.Path]) -> list[str]:
    errors = []
    for path in paths:
        if not path.is_file():
            errors.append(f"公开稿文件不存在：{path}")
            continue
        for problem in validate_public_text(path.read_text(encoding="utf-8")):
            errors.append(f"{path}: {problem}")
    return errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: validate_public_output.py <public-file> [<public-file> ...]"
        )
    problems = validate_paths([pathlib.Path(value) for value in sys.argv[1:]])
    if problems:
        print("\n".join(problems))
        raise SystemExit(1)
    print("VALID")

