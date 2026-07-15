"""Validate the non-publishing daily delivery contract."""

from validate_public_output import validate_public_text


REQUIRED_HEADINGS = (
    "# Lee连锁智库日报",
    "## 创作入口",
    "## 数据覆盖说明",
    "## 选题雷达",
    "## 今日推荐题",
    "## 今日待审稿",
    "## 证据审核台账（仅内部审核，不进入成稿）",
    "## 编辑附注",
)

EVIDENCE_LABELS = (
    "[事实]",
    "[内部观察]",
    "[内部素材]",
    "[推断]",
)

BANNED_PUBLICATION_PHRASES = (
    "已自动发布",
    "自动发布到公众号",
    "已发布到公众号",
    "已发布到小红书",
    "已发布到抖音",
)


def validate_report(markdown: str) -> list[str]:
    errors = []
    for heading in REQUIRED_HEADINGS:
        if heading not in markdown:
            errors.append(f"缺少{heading.lstrip('# ').strip()}")
    if "状态：待 Lee 审核" not in markdown:
        errors.append("缺少待 Lee 审核状态")
    if any(phrase in markdown for phrase in BANNED_PUBLICATION_PHRASES):
        errors.append("出现禁止的自动发布表述")
    draft = markdown.partition("## 今日待审稿")[2].partition(
        "## 证据审核台账（仅内部审核，不进入成稿）"
    )[0]
    if any(label in draft for label in EVIDENCE_LABELS):
        errors.append("成稿正文不应显示证据标签")
    errors.extend(validate_public_text(draft))
    return list(dict.fromkeys(errors))


if __name__ == "__main__":
    import pathlib
    import sys

    report_path = pathlib.Path(sys.argv[1])
    problems = validate_report(report_path.read_text(encoding="utf-8"))
    if problems:
        print("\n".join(problems))
        raise SystemExit(1)
    print("VALID")

