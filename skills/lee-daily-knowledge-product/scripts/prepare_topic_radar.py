"""Prepare Lee's topic radar from Creator Buddy discovery data."""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, datetime


def _text(item: dict) -> str:
    return " ".join(
        str(item.get(key, "")) for key in ("title", "summary", "matchedKeyword")
    ).lower()


def _normalize_name(value: object) -> str:
    return "".join(str(value or "").lower().split())


def _exclusions(text: str, config: dict) -> list[str]:
    matches = []
    for category, cues in config["exclusion_cues"].items():
        for cue in cues:
            if cue.lower() in text:
                if category not in matches:
                    matches.append(category)
                matches.append(f"{category}语境：{cue}")
    return matches


def match_priority_account(account_name: str, source_pool: dict) -> str:
    normalized = _normalize_name(account_name)
    if not normalized:
        return ""
    for account in source_pool.get("priority_accounts", []):
        aliases = account.get("aliases", [])
        if any(_normalize_name(alias) == normalized for alias in aliases):
            return str(account.get("canonical_name", ""))
    return ""


def _parse_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _content_window(
    published_at: object, run_date: date, is_enterprise_case: bool
) -> tuple[str, str]:
    published_date = _parse_date(published_at)
    if published_date is None:
        return "unknown", "缺少有效发布时间，需人工复核"
    age_days = (run_date - published_date).days
    if age_days < 0:
        return "unknown", "发布时间晚于运行日，需人工复核"
    if age_days <= 2:
        return "hot_3d", "近三日热点窗口"
    if age_days <= 29 and is_enterprise_case:
        return "case_30d", "近三十日企业案例窗口"
    return "background", "超出可直接选题窗口，仅作背景"


def mark_candidate(
    item: dict, config: dict, source_pool: dict, run_date: date
) -> dict:
    text = _text(item)
    excluded = _exclusions(text, config)
    positive = [cue for cue in config["positive_cues"] if cue.lower() in text]
    case_cues = [
        cue
        for cue in source_pool.get("enterprise_case_cues", [])
        if cue.lower() in text
    ]
    is_enterprise_case = bool(case_cues)
    window, window_reason = _content_window(
        item.get("publicTime", ""), run_date, is_enterprise_case
    )
    account_name = str(item.get("accountName", "") or "")
    priority_account = match_priority_account(account_name, source_pool)

    if excluded:
        status = "excluded"
        reasons = excluded
    elif window in {"unknown", "background"}:
        status = "manual_review"
        reasons = [window_reason]
    elif positive:
        status = "candidate"
        reasons = [f"主题线索：{cue}" for cue in positive]
    else:
        status = "manual_review"
        reasons = ["缺少明确主题语境，需人工复核"]

    if case_cues:
        reasons.append(f"企业案例线索：{case_cues[0]}")
    if priority_account:
        reasons.append(f"重点公众号：{priority_account}")

    return {
        "title": item.get("title", ""),
        "matched_keyword": item.get("matchedKeyword", ""),
        "account_name": account_name,
        "priority_account": priority_account,
        "source_role": "topic_lead",
        "topic_type": "enterprise_case" if is_enterprise_case else "topic_signal",
        "content_window": window,
        "published_at": item.get("publicTime", ""),
        "source_url": item.get("oriUrl") or item.get("noteLink", ""),
        "verification_required": True,
        "relevance_status": status,
        "relevance_reasons": reasons,
    }


def _dedupe(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for item in candidates:
        key = _normalize_name(item.get("source_url")) or _normalize_name(
            item.get("title")
        )
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(item)
    return result


def _apply_account_cap(candidates: list[dict], source_pool: dict) -> None:
    cap = int(source_pool.get("candidate_cap_per_account", 2))
    counts: dict[str, int] = {}
    for item in candidates:
        account = item.get("priority_account", "")
        if not account or item.get("relevance_status") != "candidate":
            continue
        counts[account] = counts.get(account, 0) + 1
        if counts[account] > cap:
            item["relevance_status"] = "manual_review"
            item["relevance_reasons"].append(
                f"同一重点账号每日候选上限为 {cap}，需人工复核"
            )


def prepare_topic_radar(
    source_data: dict,
    config: dict,
    source_pool: dict,
    run_date: str | None = None,
) -> dict:
    effective_run_date = _parse_date(run_date or source_data.get("reportDate"))
    if effective_run_date is None:
        effective_run_date = date.today()

    candidates = []
    matched_accounts: set[str] = set()
    for sector in source_data.get("sectors", []):
        for item in sector.get("items", []):
            marked = mark_candidate(item, config, source_pool, effective_run_date)
            marked["sector"] = sector.get("name", "")
            if marked["priority_account"]:
                matched_accounts.add(marked["priority_account"])
            candidates.append(marked)

    candidates = _dedupe(candidates)
    _apply_account_cap(candidates, source_pool)

    configured_accounts = [
        str(account.get("canonical_name", ""))
        for account in source_pool.get("priority_accounts", [])
        if account.get("canonical_name")
    ]
    return {
        "candidates": candidates,
        "source_coverage": {
            "matched": [
                account for account in configured_accounts if account in matched_accounts
            ],
            "missing": [
                account for account in configured_accounts if account not in matched_accounts
            ],
        },
    }


if __name__ == "__main__":
    if len(sys.argv) not in {5, 6}:
        raise SystemExit(
            "Usage: prepare_topic_radar.py <data.json> <topic-config.json> "
            "<source-pool.json> <output.json> [run-date]"
        )
    source_path = pathlib.Path(sys.argv[1])
    config_path = pathlib.Path(sys.argv[2])
    source_pool_path = pathlib.Path(sys.argv[3])
    output_path = pathlib.Path(sys.argv[4])
    source = json.loads(source_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_pool = json.loads(source_pool_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            prepare_topic_radar(
                source, config, source_pool, sys.argv[5] if len(sys.argv) == 6 else None
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

