"""Return whether today's Lee daily report must short-circuit the pipeline."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import tempfile

from validate_daily_report import validate_report


EXACT_REVIEW_STATUS = "状态：待 Lee 审核；未自动发布。"
SEAL_FILENAME = "lee-daily-delivery-seal.json"
SEAL_SCHEMA_VERSION = 1
VALIDATOR_PATH = pathlib.Path(__file__).with_name("validate_daily_report.py")


def _file_signature(path: pathlib.Path) -> dict:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _seal_path_for(report_path: pathlib.Path) -> pathlib.Path:
    return report_path.parent / SEAL_FILENAME


def _read_seal(seal_path: pathlib.Path) -> dict | None:
    if not seal_path.is_file():
        return None
    try:
        value = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _seal_matches(
    seal: dict | None,
    report_path: pathlib.Path,
) -> bool:
    if not seal:
        return False
    return (
        seal.get("schema_version") == SEAL_SCHEMA_VERSION
        and seal.get("review_status") == EXACT_REVIEW_STATUS
        and seal.get("report") == _file_signature(report_path)
        and seal.get("validator") == _file_signature(VALIDATOR_PATH)
    )


def _write_seal(seal_path: pathlib.Path, report_path: pathlib.Path) -> None:
    payload = {
        "schema_version": SEAL_SCHEMA_VERSION,
        "review_status": EXACT_REVIEW_STATUS,
        "validated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "report": _file_signature(report_path),
        "validator": _file_signature(VALIDATOR_PATH),
    }
    seal_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=seal_path.parent,
            prefix=f".{seal_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = pathlib.Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, seal_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _discard_seal(seal_path: pathlib.Path) -> None:
    try:
        seal_path.unlink()
    except FileNotFoundError:
        pass


def inspect_daily_report(
    report_path: pathlib.Path,
    *,
    validate_fn=None,
) -> dict:
    seal_path = _seal_path_for(report_path)
    if not report_path.is_file():
        _discard_seal(seal_path)
        return {
            "already_delivered": False,
            "reason": "report_missing",
            "validation_errors": [],
            "validation_skipped": True,
        }

    if _seal_matches(_read_seal(seal_path), report_path):
        return {
            "already_delivered": True,
            "reason": "validated_delivery_seal_matches",
            "validation_errors": [],
            "validation_skipped": True,
        }

    markdown = report_path.read_text(encoding="utf-8")
    if EXACT_REVIEW_STATUS not in markdown:
        _discard_seal(seal_path)
        return {
            "already_delivered": False,
            "reason": "status_mismatch",
            "validation_errors": [],
            "validation_skipped": False,
        }

    if validate_fn is None:
        validate_fn = validate_report
    errors = validate_fn(markdown)
    if errors:
        _discard_seal(seal_path)
        return {
            "already_delivered": False,
            "reason": "report_invalid",
            "validation_errors": errors,
            "validation_skipped": False,
        }

    _write_seal(seal_path, report_path)
    return {
        "already_delivered": True,
        "reason": "valid_review_report_sealed",
        "validation_errors": [],
        "validation_skipped": False,
    }


if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1])
    print(json.dumps(inspect_daily_report(path), ensure_ascii=False))
