"""Read-only validation and change detection for Lee sir registered sources."""
import hashlib
import json
import re
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    kb = root / 'references' / 'knowledge'
    manifest = json.loads((kb / 'sources.json').read_text(encoding='utf-8'))
    support = json.loads((root / 'references' / 'support-sources.json').read_text(encoding='utf-8'))
    reading_path = root / 'references' / 'full-reading-sources.json'
    reading = json.loads(reading_path.read_text(encoding='utf-8')) if reading_path.exists() else {'sources': []}
    changed, missing, broken, unknown = [], [], [], []
    known = {s['id'] for s in manifest['sources']}
    support_known = {s['id'] for s in support['sources']}
    reading_known = {s['id'] for s in reading['sources']}
    for source in manifest['sources'] + support['sources'] + reading['sources']:
        path = Path(source['path'])
        if not path.is_file():
            missing.append(source['id'])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            changed.append(source['id'])
    pages = [root / 'SKILL.md', *list((root / 'references').rglob('*.md'))]
    for page in pages:
        body = page.read_text(encoding='utf-8')
        for sid in set(re.findall(r'\bSP\d{2}\b', body)) - support_known:
            unknown.append({'file': str(page.relative_to(root)), 'source_id': sid})
        for sid in set(re.findall(r'\bFR\d{2}\b', body)) - reading_known:
            unknown.append({'file': str(page.relative_to(root)), 'source_id': sid})
        for target in re.findall(r'\]\(([^)]+)\)', body):
            target = target.strip('<>')
            if '://' in target or re.match(r'^[A-Za-z]:', target):
                continue
            target = target.split('#')[0]
            if target and not (page.parent / target).exists():
                broken.append({'file': str(page.relative_to(root)), 'target': target})
        if page.parent == kb and page.name not in {'source-map.md'}:
            for sid in set(re.findall(r'\bS\d{2}\b', body)) - known:
                unknown.append({'file': page.name, 'source_id': sid})
    modules = sorted(kb.glob('[0-9][0-9]-*.md'))
    cards = sum(len(re.findall(r'^## K\d{2}\.\d+｜', p.read_text(encoding='utf-8'), re.M)) for p in modules)
    report = {
        'modules': len(modules), 'cards': cards,
        'registered_sources': len(known), 'changed_sources': changed,
        'support_sources': len(support_known),
        'full_reading_reviewed_sources': len(reading_known),
        'missing_sources': missing, 'broken_links': broken,
        'unknown_source_ids': unknown,
        'duplicate_skill_entries': [str(p.relative_to(root)) for p in root.rglob('SKILL.md') if p != root / 'SKILL.md'],
        'note': '结构与变化检测，不证明知识正确或决策有效；不修改基线。'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if missing or broken or unknown or report['duplicate_skill_entries'] else (2 if changed else 0)


if __name__ == '__main__':
    raise SystemExit(main())
