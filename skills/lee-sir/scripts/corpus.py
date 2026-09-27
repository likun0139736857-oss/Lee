"""Local, resumable corpus preparation. Extraction never counts as close reading."""
import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

BASE = Path('D:/BaiduSyncdisk')
OUT = BASE / 'outputs' / 'lee-sir-full-reading'
DB = OUT / 'coverage.sqlite'
SUPPORTED = {'.md', '.txt', '.csv', '.srt', '.org', '.html', '.htm', '.docx', '.docm', '.pptx', '.xlsx', '.pdf', '.epub'}


def metadata_only(path):
    return bool(re.search(r'密码|口令|密钥|秘钥|凭据|credential|secret|token|^\.env', Path(path).name, re.I))


def redact(text):
    text = re.sub(r'(?i)([?&](?:code|token|key|authcode|access_token|signature)=)[^&\s)"<>]+', r'\1[REDACTED]', text)
    return re.sub(r'(?i)((?:password|api[_ -]?key|access[_ -]?token|密码|口令|密钥)\s*[:=：]\s*)[^\s,，;；]+', r'\1[REDACTED]', text)


def connect():
    OUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, kind TEXT, ext TEXT, size INTEGER, mtime INTEGER, state TEXT, hash TEXT, body TEXT, detail TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS reviews(path TEXT, hash TEXT, start INTEGER, stop INTEGER, artifact TEXT, note TEXT, reviewed_at TEXT)')
    if 'present' not in {r[1] for r in db.execute('PRAGMA table_info(files)')}:
        db.execute('ALTER TABLE files ADD COLUMN present INTEGER NOT NULL DEFAULT 1')
        db.commit()
    return db


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def decode(data):
    for enc in ('utf-8-sig', 'utf-16', 'gb18030'):
        try:
            t = data.decode(enc)
            if '\x00' not in t: return t
        except UnicodeError: pass
    raise ValueError('unrecognized text encoding')


def extract_one(path):
    p = Path(path)
    try:
        if metadata_only(p): return (None,None,'Credential-labelled material: retain path only','credential_metadata_only',path)
        before = p.stat()
        digest = hashlib.sha256()
        with p.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
        sha = digest.hexdigest()
        parts = []; detail = []; ext = p.suffix.lower()
        if ext in {'.md', '.txt', '.csv', '.srt', '.org'}: parts = [decode(p.read_bytes())]
        elif ext in {'.html', '.htm'}:
            parser = HTMLText(); parser.feed(decode(p.read_bytes())); parts = parser.parts
            detail.append('HTML text only; images and linked content unreviewed')
        elif ext == '.pdf':
            import pymupdf as fitz
            fitz.TOOLS.mupdf_display_errors(False)
            fitz.TOOLS.mupdf_display_warnings(False)
            fitz.TOOLS.reset_mupdf_warnings()
            with fitz.open(p) as doc:
                if doc.needs_pass: raise ValueError('encrypted PDF')
                empty = []; images = []
                for n, page in enumerate(doc, 1):
                    t = page.get_text(sort=True)
                    if not t.strip(): empty.append(n)
                    if page.get_images(): images.append(n)
                    parts.append(f'\n[PAGE {n}]\n{t}')
                detail.append(json.dumps({'pages':len(doc),'empty_text_pages':empty,'image_pages':images}))
                detail.append('PDF text layer only; visual reading/OCR still required')
                if fitz.TOOLS.mupdf_warnings(): detail.append('Parser warnings occurred; completeness uncertain')
        else:
            with ZipFile(p) as z:
                names = z.namelist()
                if ext in {'.docx', '.docm'}:
                    wanted = [x for x in names if x == 'word/document.xml' or (x.startswith(('word/header','word/footer','word/footnotes','word/endnotes','word/comments')) and x.endswith('.xml'))]
                elif ext == '.pptx':
                    wanted = sorted([x for x in names if x.startswith(('ppt/slides/slide','ppt/notesSlides/notesSlide')) and x.endswith('.xml') and '/_rels/' not in x])
                elif ext == '.xlsx':
                    shared = []
                    if 'xl/sharedStrings.xml' in names:
                        shared = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml'))]
                    for name in sorted(x for x in names if x.startswith('xl/worksheets/sheet') and x.endswith('.xml')):
                        parts.append('\n[SHEET '+name+']')
                        for cell in ET.fromstring(z.read(name)).iter():
                            if cell.tag.split('}')[-1] != 'c': continue
                            vals = [n.text or '' for n in cell if n.tag.split('}')[-1] in ('v','f')]
                            if cell.attrib.get('t') == 's' and vals:
                                vals = [shared[int(vals[-1])]] if vals[-1].strip() else []
                            if cell.attrib.get('t') == 'inlineStr': vals = [''.join(cell.itertext())]
                            if vals: parts.append(cell.attrib.get('r','')+' | '+' | '.join(vals))
                    wanted = []
                    detail.append('Cell values/formulas only; cached formulas may be stale; charts/layout unreviewed')
                else: wanted = sorted(x for x in names if x.lower().endswith(('.html','.xhtml','.htm')))
                for name in wanted:
                    if ext == '.epub':
                        parser = HTMLText();parser.feed(decode(z.read(name))); t = ''.join(parser.parts)
                    else:
                        root = ET.fromstring(z.read(name))
                        t = '\n'.join(n.text for n in root.iter() if n.tag.split('}')[-1] in ('t','instrText') and n.text)
                    parts.append('\n[PART '+name+']\n'+t)
                media = sum('/media/' in x or x.lower().endswith(('.png','.jpg','.jpeg')) for x in names)
                detail.append(f'text extraction; {media} embedded media entries not visually read')
        body = redact('\n'.join(parts))
        after = p.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ValueError('source changed during extraction; retry required')
        import re
        payload = re.sub(r'\[(?:PAGE|PART|SHEET) [^\]]+\]', '', body).strip()
        return (sha,body,'; '.join(detail),'text_extracted' if payload else 'empty_text',path)
    except Exception as exc:
        return (None,None,str(exc)[:600],'extraction_failed',path)


def scan(db):
    totals = Counter(); extensions = Counter(); count = 0; size = 0
    db.execute('UPDATE files SET present=0')
    for p in BASE.rglob('*'):
        if not p.is_file() or OUT in p.parents: continue
        rel = p.relative_to(BASE); first = rel.parts[0]; st = p.stat()
        kind = 'source' if first in ('raw','clippings') else ('derived' if first == 'wiki' else 'operational')
        old = db.execute('SELECT size,mtime FROM files WHERE path=?',(str(p),)).fetchone()
        if old != (st.st_size, st.st_mtime_ns):
            state = 'pending_extraction' if kind == 'source' and p.suffix.lower() in SUPPORTED else 'inventoried'
            if metadata_only(p): state = 'credential_metadata_only'
            db.execute('INSERT OR REPLACE INTO files(path,kind,ext,size,mtime,state,hash,body,detail,present) VALUES (?,?,?,?,?,?,NULL,NULL,NULL,1)',(str(p),kind,p.suffix.lower(),st.st_size,st.st_mtime_ns,state))
        else:
            db.execute('UPDATE files SET present=1 WHERE path=?',(str(p),))
        totals[first] += 1; count += 1
        if kind == 'source': size += st.st_size; extensions[p.suffix.lower()] += 1
    db.commit()
    report = dict(snapshot=time.strftime('%Y-%m-%d %H:%M:%S'),files=count,source_bytes=size,by_root=dict(totals),source_extensions=dict(extensions),note='Inventory only, not reading. Excludes this task output directory. Old records are retained for traceability.')
    (OUT/'inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


def status(db):
    rows = db.execute('SELECT state,count(*) FROM files WHERE kind="source" AND present=1 GROUP BY state').fetchall()
    current = db.execute('SELECT count(DISTINCT r.path) FROM reviews r JOIN files f ON f.path=r.path AND f.hash=r.hash WHERE f.present=1').fetchone()[0]
    report = {'updated_at':time.strftime('%Y-%m-%d %H:%M:%S'),'source_states':rows,'review_records':db.execute('SELECT count(*) FROM reviews').fetchone()[0], 'current_text_reviewed_files':current, 'note':'Text extraction is not close reading. Reviews cover specified text ranges only, with source hash and synthesis artifact; embedded media limitations remain.'}
    (OUT/'status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['scan','extract','status','read','review']);ap.add_argument('--path');ap.add_argument('--start',type=int,default=0);ap.add_argument('--stop',type=int);ap.add_argument('--artifact');ap.add_argument('--note',default='');ap.add_argument('--limit',type=int,default=0);ap.add_argument('--workers',type=int,default=3)
    a=ap.parse_args();db=connect()
    if a.action=='scan': scan(db)
    elif a.action=='extract':
        paths=[x[0] for x in db.execute('SELECT path FROM files WHERE state="pending_extraction" AND present=1 ORDER BY size')]
        if a.limit: paths=paths[:a.limit]
        with ProcessPoolExecutor(max_workers=a.workers) as pool:
            for i,result in enumerate(pool.map(extract_one,paths,chunksize=1),1):
                db.execute('UPDATE files SET hash=?,body=?,detail=?,state=? WHERE path=?',result)
                if i%50==0: db.commit();print('extracted',i,'/',len(paths),flush=True)
        db.commit();status(db)
    elif a.action=='status': status(db)
    else:
        row=db.execute('SELECT hash,body,detail FROM files WHERE path=?',(a.path,)).fetchone()
        if not row or row[1] is None: raise ValueError('No extracted body for exact path')
        sha,body,detail=row;stop=len(body) if a.stop is None else min(a.stop,len(body))
        if a.action=='read': print(json.dumps(dict(path=a.path,sha256=sha,start=a.start,stop=stop,total_chars=len(body),limitations=detail),ensure_ascii=False));print(redact(body[a.start:stop]))
        else:
            if not a.artifact or not Path(a.artifact).is_file(): raise ValueError('Reviewed synthesis artifact must exist')
            if not 0 <= a.start < stop <= len(body): raise ValueError('Invalid reviewed range')
            if hashlib.sha256(Path(a.path).read_bytes()).hexdigest()!=sha: raise ValueError('Source changed since extraction')
            db.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?,?)',(a.path,sha,a.start,stop,a.artifact,a.note,time.strftime('%Y-%m-%d %H:%M:%S')));db.commit();print('Review recorded; source format limitations remain.')
    db.close()


if __name__=='__main__': main()
