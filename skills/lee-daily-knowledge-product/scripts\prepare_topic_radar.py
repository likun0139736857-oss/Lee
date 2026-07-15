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
            if marked["priority_accou…3169 tokens truncated…中间工序，不沿用其默认署名、个人目录或对外写入。
8. 主笔调用 `ljg-writes` **或** `sansheng-write` 成文：前者适合单一判断，后者适合完整长文工作流。本技能的“仅审核、不发布”约束覆盖其他 Skill 的默认发布动作。
9. 进行 Lee 专属风格打磨，再调用 `humanizer-zh`：从真实场景切入，写清“判断 → 依据 → 风险 → 动作 → 验证指标”，保留不确定性与反方校验；不强行绑定连锁行业，未指定时允许从普适生活、组织或认知切入。随后完成公开稿匿名化与反向识别检查。
10. **事实核查**在润色后逐句复核文章与证据台账；**资深主编**终审标题、逻辑、可读性、行动性、争议公平性和 Gold Line。任一核心 Gold Line 项（1、3、5、8）不通过，稿件标为 `退回修改`。
11. 只有终审通过后才调用 `gzh-design` 输出 `article.md`、公众号 HTML 和预览页。依次运行：

    ```text
    validate_daily_report.py <report.md>
    validate_public_output.py <article.md> <article.html> <preview.html>
    ```

    两项均输出 `VALID` 才能在当前任务推送摘要与文件链接；仍不得发布。

## Decision Rules

| 情况 | 动作 |
| --- | --- |
| “加盟”命中体育、娱乐或人员转会 | 保留在雷达供审计，标为 `excluded`，不选题 |
| 重点公众号无命中 | 写进覆盖缺口；改用其主题关键词补检，但不得伪称抓到该账号文章 |
| 公众号文章提供数据、结论或争议判断 | 只作为线索；回查一手/权威来源后才能作为事实 |
| 热点超出三日窗口 | 普通热点降为背景；符合企业案例标准且在 30 日内时可保留 |
| 企业案例只有品牌宣传材料 | 标为线索或推断；补到独立证据后才能确定性成文 |
| 争议缺少反方材料或原始回应 | 降低措辞确定性并列为缺口；核心争议不得直接定性 |
| 私有笔记含个体、合同、利润或纠纷 | 只形成匿名内部素材；无法匿名化则不用 |
| Lee 的观点或初稿含外部断言 | 先作为内部素材记账；核验后才可作为公开事实 |
| 公开稿出现真实业务品牌名或可反向识别组合 | 退回匿名化，不得进入 `gzh-design` 交付 |
| 证据不足但题目有启发 | 可做“待验证假设”，不可写成“行业已发生” |

## Role Order

按以下顺序执行：素材路由 → 风格总监 → 选题总监 → 调研专家 → 企业案例分析 → 知识管家 → LJG 提炼 → 主笔 → Lee 风格编辑 + `humanizer-zh` → 匿名化检查 → 事实核查 → 资深主编 → `gzh-design` 排版审查。不能跳过调研核验、证据分层、匿名化、事实核查、终审或公开稿校验。角色输入、输出和否决权见 `references/roles-and-quality-gates.md`。

## Final Task Message

只发送：创作入口、推荐题或 Lee 投喂主题、关键判断、重点账号覆盖缺口、权威/争议核验缺口、质量闸门结果、待审状态和报告路径。不要发送内部映射、私有原文、真实业务品牌名或自动发布动作。
