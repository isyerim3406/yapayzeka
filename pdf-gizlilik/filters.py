"""Bundled, editable offline rules. No network or backups."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata


def normalize(text):
    text = unicodedata.normalize('NFKD', text.translate(str.maketrans('ıİ', 'ii'))).lower()
    return ''.join(c for c in text if c.isalnum())


def default_path():
    return Path(__file__).with_name('default_filters.json')


def user_path():
    root = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
    return root / 'PDF-Gizlilik' / 'filters.json'


def validate(data):
    if not isinstance(data, dict) or data.get('schema_version') != '1.0':
        raise ValueError('Filtre dosyası biçimi desteklenmiyor.')
    seen = set()
    for key in ('rules', 'user_rules'):
        if not isinstance(data.get(key), list):
            raise ValueError('Filtre listesi eksik.')
        for r in data[key]:
            if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] or r['id'] in seen:
                raise ValueError('Filtre kimliği geçersiz veya tekrarlı.')
            seen.add(r['id'])
            if r.get('kind') not in ('field', 'literal', 'regex'):
                raise ValueError('Geçersiz filtre türü.')
            if not isinstance(r.get('name'), str) or not r['name'].strip():
                raise ValueError('Filtre adı eksik.')
            if not isinstance(r.get('category'), str) or not isinstance(r.get('enabled'), bool):
                raise ValueError('Filtre kategori/durum bilgisi geçersiz.')
            if not isinstance(r.get('aliases'), list) or not all(isinstance(a, str) and a.strip() for a in r['aliases']):
                raise ValueError('Başlık/ifade listesi geçersiz.')
            if r['kind'] != 'regex' and not r['aliases']:
                raise ValueError('En az bir başlık veya ifade girin.')
            if type(r.get('max_lines')) is not int or not 1 <= r['max_lines'] <= 10:
                raise ValueError('Satır sayısı 1–10 olmalı.')
            if r['kind'] == 'regex':
                # Custom regex is deliberately not accepted: user rules use safe literals/labels.
                if key == 'user_rules':
                    raise ValueError('Kullanıcı filtresi başlık veya birebir ifade olmalı.')
                re.compile(r['pattern'])
    return data


class FilterStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else user_path()
        self.defaults = validate(json.loads(default_path().read_text(encoding='utf-8')))
        self.error = None
        try:
            self.data = validate(json.loads(self.path.read_text(encoding='utf-8'))) if self.path.exists() else copy.deepcopy(self.defaults)
        except (ValueError, OSError) as exc:
            self.error = str(exc)
            self.data = copy.deepcopy(self.defaults)

    def snapshot(self):
        return copy.deepcopy(self.data)

    def save(self, data):
        validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.filters-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(data, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)
        self.data = copy.deepcopy(data)
        self.error = None


def _rows(lines):
    """Combine OCR blocks by geometric baseline; retain each word rectangle."""
    words, seen = [], set()
    for text, mapping in lines:
        for a, b, rect in mapping:
            value = text[a:b]
            key = (value, tuple(round(v, 1) for v in rect))
            if key not in seen:
                seen.add(key)
                words.append((value, rect))
    rows = []
    for word in sorted(words, key=lambda w: ((w[1][1]+w[1][3])/2, w[1][0])):
        cy = (word[1][1]+word[1][3])/2
        height = word[1][3]-word[1][1]
        target = next((r for r in reversed(rows[-4:]) if abs(r['cy']-cy) <= max(2, min(r['h'],height)*.40)), None)
        if target is None:
            target = dict(cy=cy,h=max(1,height),words=[])
            rows.append(target)
        target['words'].append(word)
    for row in rows:
        row['words'].sort(key=lambda w:w[1][0])
    return sorted(rows,key=lambda r:r['cy'])


def _box(ws):
    return (min(w[1][0] for w in ws), min(w[1][1] for w in ws), max(w[1][2] for w in ws), max(w[1][3] for w in ws))


class FilterMatcher:
    def __init__(self, data):
        validate(data)
        # Disabled labels remain boundaries, so a preceding field cannot eat them.
        self.fields=[]
        self.text_rules=[]
        for r in data['rules']+data['user_rules']:
            if r['kind']=='field':
                for alias in r['aliases']:
                    self.fields.append((normalize(alias),r))
            elif r['enabled']:
                pats=[re.compile(r['pattern'], r.get('regex_flags',0))] if r['kind']=='regex' else [re.compile(re.escape(a),re.I) for a in r['aliases']]
                self.text_rules.append((r,pats))
        self.fields.sort(key=lambda x:len(x[0]),reverse=True)

    def find(self, lines, page, source):
        from engine import Finding, _tckn, _is_candidate
        rows=_rows(lines); output=[]; anchors=[]
        for ri,row in enumerate(rows):
            ws=row['words']; used=set()
            for start in range(len(ws)):
                if start in used:continue
                # Heading starts a cell, not an arbitrary word in running prose.
                if start and ws[start][1][0]-ws[start-1][1][2] < max(10,row['h']*.8):continue
                for alias,r in self.fields:
                    acc=''
                    for end in range(start,min(len(ws),start+12)):
                        acc+=normalize(ws[end][0])
                        if len(acc)>len(alias):break
                        if acc==alias:
                            anchors.append((ri,start,end+1,r))
                            used.update(range(start,end+1));break
                    else:continue
                    if acc==alias:break
        for ri,start,end,r in anchors:
            if not r['enabled']:continue
            row=rows[ri];ws=row['words'];right=len(ws)
            later=[a for a in anchors if a[0]==ri and a[1]>=end]
            if later:right=min(a[1] for a in later)
            values=[w for w in ws[end:right] if normalize(w[0])]
            # Prefer visible same-row value. Below-label values must align with label's column.
            label_left=ws[start][1][0]
            boundary=min((rows[a[0]]['words'][a[1]][1][0] for a in anchors if abs(rows[a[0]]['cy']-row['cy'])<80 and rows[a[0]]['words'][a[1]][1][0]>label_left+80),default=float('inf'))
            if later:boundary=ws[right][1][0]
            values=[w for w in values if w[1][0]<boundary]
            value_left=values[0][1][0] if values else label_left
            groups=[values] if values else []
            previous=row
            limit=r.get('max_lines',2) if r.get('multiline') else 1
            # One following row also supports label-above-value forms.
            for ni in range(ri+1,min(len(rows),ri+1+(limit-len(groups)))):
                nr=rows[ni]
                if nr['cy']-previous['cy']>max(row['h'],nr['h'])*2.0:break
                if any(a[0]==ni and value_left-12 <= nr['words'][a[1]][1][0]<boundary for a in anchors):break
                candidates=[w for w in nr['words'] if value_left-12<=w[1][0]<boundary]
                if not candidates or abs(candidates[0][1][0]-value_left)>max(15,row['h']):break
                groups.append(candidates);previous=nr
            for group in groups:
                value=' '.join(w[0] for w in group).strip(' :=')
                if value and normalize(value) not in ('yok','bulunmamaktadir','none'):
                    output.append(Finding(page,_box(group),r['category'],value,source+' / '+r['name']))
        for row in rows:
            text='';mapping=[]
            for w in row['words']:
                if text:text+=' '
                a=len(text);text+=w[0];mapping.append((a,len(text),w[1]))
            for r,pats in self.text_rules:
                for pat in pats:
                    for match in pat.finditer(text):
                        if r["category"] == "TCKN" and not _tckn(match.group()):continue
                        if not _is_candidate(r["category"], match.group()):continue
                        group=[(text[a:b],rect) for a,b,rect in mapping if b>match.start() and a<match.end()]
                        if group:output.append(Finding(page,_box(group),r['category'],match.group(),source+' / '+r['name']))
        return output
