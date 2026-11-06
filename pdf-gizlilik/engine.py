"""Offline PDF redaction engine. All scans and source documents stay in local memory.
No telemetry, no backups, no permanent logs of source text.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections import defaultdict
import re
import shutil
import sys
import os

import fitz

CATEGORY_ORDER = (
    'Koordinat', 'Ada/Parsel', 'Firma/Tesis', 'Adres/Konum',
    'Kişi', 'TCKN', 'VKN/MERSİS', 'Telefon', 'E-posta', 'IBAN', 'Özel terim'
)

# A rule always identifies actual sensitive text, rather than using the PDF's
# location as a proxy for trust. The user must still review non-text map cues.
PATTERNS = [
    ('Koordinat', re.compile(r'(?<!\w)(?:X|Y|E|N|DOĞU|KUZEY|EASTING|NORTHING)\s*[:=]\s*[-+]?\d{3,8}(?:[.,]\d{1,7})?', re.I)),
    ('Koordinat', re.compile(r'(?<!\d)(?:[3-4]\d)[.,]\d{4,8}\s*[,;/\s]+\s*(?:[2-4]\d)[.,]\d{4,8}(?!\d)')),
    ('Koordinat', re.compile(r'(?<!\d)\d{5,7}(?:[.,]\d{1,4})?\s*[,;/\s]+\s*\d{6,7}(?:[.,]\d{1,4})?(?!\d)')),
    ('Koordinat', re.compile(r'(?<!\w)\d{1,2}\s*[°º]\s*\d{1,2}\s*[\'′]\s*\d{1,2}(?:[.,]\d+)?\s*(?:["″])?\s*[NSEW]?', re.I)),
    ('Koordinat', re.compile(r'\b(?:ED\s*50|ITRF\s*96?|WGS\s*84|UTM)\s*(?:koordinat(?:ları|ları)?|datum|sistemi|zone|dilim)?\s*[:=]?\s*\d{1,7}(?:[.,]\d+)?', re.I)),
    ('Ada/Parsel', re.compile(r'\b(?:\d{1,7}\s*(?:ada)\s*[,/\- ]*\s*\d{1,7}\s*(?:parsel)|ada\s*(?:no|numarası)?\s*[:#]?\s*\d{1,7}\s*(?:[,;/\- ]+parsel\s*(?:no|numarası)?\s*[:#]?\s*\d{1,7})?)\b', re.I)),
    ('Ada/Parsel', re.compile(r'\b(?:pafta\s*(?:no|numarası)?\s*[:=]?\s*[A-Z0-9\-/]{3,20})\b', re.I)),
    ('Firma/Tesis', re.compile(r'\b(?:[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü&.\-]*\s+){1,8}(?:SANAYİ\s+VE\s+TİCARET\s+)?(?:A\.?Ş\.?|LTD\.?\s*ŞTİ\.?|LİMİTED\s+ŞİRKETİ|ANONİM\s+ŞİRKETİ)\b', re.I)),
    ('Firma/Tesis', re.compile(r'\b(?:Firma\s*Unvanı|Tesis\s*Adı|Proje\s*Sahibi|Başvuru\s*Sahibi|İşletme\s*Adı)\s*[:=]\s*[^;|]{3,110}', re.I)),
    ('Adres/Konum', re.compile(r'\b(?:Tesis\s*Adresi|Açık\s*Adres|Adres|İl\s*/\s*İlçe|Mahalle|Mevkii|Köyü|Beldesi)\s*[:=]\s*[^;|]{3,130}', re.I)),
    ('Adres/Konum', re.compile(r'\b(?:[\wÇĞİÖŞÜçğıöşü]+\s+){0,5}(?:Mahallesi|Mah\.|Caddesi|Cad\.|Sokak|Sok\.|Mevkii)\s*(?:No\s*[:.]?\s*\d+[A-Za-z/\d-]*)?', re.I)),
    ('Kişi', re.compile(r'\b(?:Adı\s*Soyadı|İlgili\s*Kişi|Yetkili\s*Kişi|Proje\s*Müellifi|Hazırlayan|İmza\s*Sahibi)\s*[:=]\s*[^;|]{3,80}', re.I)),
    ('VKN/MERSİS', re.compile(r'\b(?:VKN|Vergi\s*Numarası|Vergi\s*Kimlik\s*No|MERSİS\s*(?:No|Numarası)?|Ticaret\s*Sicil\s*(?:No|Numarası)?)\s*[:=]?\s*[\d\s-]{7,20}', re.I)),
    ('Telefon', re.compile(r'(?<!\d)(?:\+90\s*|0\s*)?(?:\(?5\d{2}\)?[\s.\-]?)\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)')),
    ('E-posta', re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b')),
    ('IBAN', re.compile(r'\bTR\s*\d{2}(?:\s*\d{4}){5}\s*\d{2}\b|\bTR\d{24}\b', re.I)),
]

@dataclass
class Finding:
    page: int
    rect: tuple[float, float, float, float]
    category: str
    value: str
    source: str
    selected: bool = True

@dataclass
class PageWarning:
    page: int
    reason: str
    blocking: bool = True


def _tckn(value: str) -> bool:
    if len(value) != 11 or not value.isdigit() or value[0] == '0':
        return False
    x = list(map(int, value))
    return ((sum(x[0:9:2]) * 7 - sum(x[1:8:2])) % 10 == x[9]
            and sum(x[:10]) % 10 == x[10])


def _is_candidate(cat: str, s: str) -> bool:
    if cat == 'Koordinat' and len(re.findall(r'\d', s)) < 4:
        return False
    return True


def extract_lines_from_words(words):
    lines = defaultdict(list)
    for w in words:
        if len(w) >= 8:
            lines[(int(w[5]), int(w[6]))].append(w)
    for _, ws in sorted(lines.items()):
        ws.sort(key=lambda w: (w[7], w[0]))
        text, mapping = '', []
        for w in ws:
            if text:
                text += ' '
            start = len(text)
            text += str(w[4])
            mapping.append((start, len(text), tuple(map(float, w[:4]))))
        yield text, mapping


def _rect_for(mapping, a, b):
    xs = [r for start, end, r in mapping if end > a and start < b]
    if not xs:
        return None
    return (min(r[0] for r in xs), min(r[1] for r in xs),
            max(r[2] for r in xs), max(r[3] for r in xs))


def find_in_line(text, mapping, page_number, source, custom_terms=(), use_legacy=True):
    found = []
    used = set()
    patterns = list(PATTERNS) if use_legacy else []
    for term in custom_terms:
        term = term.strip()
        if len(term) >= 2:
            patterns.insert(0, ('Özel terim', re.compile(re.escape(term), re.I)))
    if use_legacy:
        patterns.append(('TCKN', re.compile(r'(?<!\d)\d{11}(?!\d)')))
    for cat, pattern in patterns:
        for m in pattern.finditer(text):
            value = m.group().strip()
            if cat == 'TCKN' and not _tckn(value):
                continue
            if not _is_candidate(cat, value):
                continue
            rect = _rect_for(mapping, m.start(), m.end())
            if rect is None:
                continue
            key = (cat, round(rect[0], 1), round(rect[1], 1), round(rect[2], 1), round(rect[3], 1))
            if key in used:
                continue
            used.add(key)
            found.append(Finding(page_number, rect, cat, value, source))
    return found


def _ocr_binary():
    # Windows portable package: OCR binaries are shipped beside the EXE.
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    candidates = [exe_dir / 'tesseract' / 'tesseract.exe',
                  exe_dir / '_internal' / 'tesseract' / 'tesseract.exe',
                  Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Tesseract-OCR' / 'tesseract.exe']
    for file in candidates:
        if file.is_file():
            return str(file)
    return shutil.which('tesseract')


def ocr_available():
    return bool(_ocr_binary())


def _ocr_lines(page, zoom=2.0, tile_pts=650, min_confidence=35):
    """Tile OCR limits memory on A0/A1 drawings. Returns line+word rectangles in points."""
    import pytesseract
    from PIL import Image
    import io
    exe = _ocr_binary()
    if not exe:
        raise RuntimeError('Tesseract OCR yok: görüntülü sayfalar güvenli kabul edilemez.')
    pytesseract.pytesseract.tesseract_cmd = exe
    langs = []
    try:
        langs = pytesseract.get_languages(config='')
    except Exception:
        pass
    lang = 'tur+eng' if 'tur' in langs and 'eng' in langs else ('tur' if 'tur' in langs else 'eng')
    rect = page.rect
    for top in range(0, int(rect.height), tile_pts):
        for left in range(0, int(rect.width), tile_pts):
            clip = fitz.Rect(rect.x0 + left, rect.y0 + top,
                             min(rect.x0 + left + tile_pts, rect.x1),
                             min(rect.y0 + top + tile_pts, rect.y1))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
            data = pytesseract.image_to_data(image, lang=lang,
                                             config='--psm 11', output_type=pytesseract.Output.DICT)
            groups = defaultdict(list)
            for i, raw in enumerate(data['text']):
                word = (raw or '').strip()
                try:
                    confidence = float(data['conf'][i])
                except (ValueError, TypeError):
                    confidence = -1
                if not word or confidence < min_confidence:
                    continue
                x0 = clip.x0 + float(data['left'][i]) / zoom
                y0 = clip.y0 + float(data['top'][i]) / zoom
                x1 = x0 + float(data['width'][i]) / zoom
                y1 = y0 + float(data['height'][i]) / zoom
                # group by OCR block/paragraph/line within a tile
                key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
                groups[key].append((x0, y0, x1, y1, word, 0, 0, data['word_num'][i]))
            for key in sorted(groups):
                yield from extract_lines_from_words(groups[key])


def scan_pdf(path: str | Path, custom_terms=(), progress=None, use_ocr=True, filter_data=None,
             external_ocr=False):
    """Returns findings and blocking warnings; never writes the original anywhere."""
    from filters import FilterMatcher, FilterStore
    matcher = FilterMatcher(filter_data if filter_data is not None else FilterStore().snapshot())
    pdf = fitz.open(str(path))
    findings, warnings = [], []
    try:
        if pdf.needs_pass:
            raise ValueError('Parolalı PDF desteklenmiyor; şifresini yerel olarak kaldırın.')
        if not pdf.is_pdf:
            raise ValueError('Yalnızca PDF desteklenir.')
        for n, page in enumerate(pdf):
            if progress:
                progress(n + 1, len(pdf), 'Metin ve pafta taraması')
            words = page.get_text('words', sort=True)
            text_lines = list(extract_lines_from_words(words))
            text_source = 'FineReader OCR' if external_ocr else 'PDF metni'
            findings.extend(matcher.find(text_lines, n, text_source))
            for text, mapping in text_lines:
                findings.extend(find_in_line(text, mapping, n, text_source, custom_terms, use_legacy=False))
            imgs = page.get_image_info(xrefs=False)
            page_area = max(1.0, page.rect.get_area())
            image_area = sum(fitz.Rect(im['bbox']).get_area() for im in imgs if 'bbox' in im)
            large_page = max(page.rect.width, page.rect.height) > 1600
            needs_ocr = (len(words) < 25 and (bool(imgs) or large_page)) or image_area > page_area * .18 or large_page
            if needs_ocr:
                if external_ocr:
                    if not words:
                        warnings.append(PageWarning(n, 'FineReader OCR: bu sayfada metin katmanı alınamadı.'))
                    elif len(words) < 10:
                        warnings.append(PageWarning(n, 'FineReader metin katmanı sınırlı; görsel inceleme gerekir.'))
                elif not use_ocr or not ocr_available():
                    warnings.append(PageWarning(n, 'Görüntü/pafta OCR taraması yapılmadı.'))
                else:
                    try:
                        ocr_lines = list(_ocr_lines(page, zoom=1.8 if large_page else 2.2))
                        ocr_findings = matcher.find(ocr_lines, n, 'OCR')
                        for text, mapping in ocr_lines:
                            ocr_findings.extend(find_in_line(text, mapping, n, 'OCR', custom_terms, use_legacy=False))
                        # OCR sees the rendered orientation. Redaction rectangles use PDF coordinates.
                        if page.rotation:
                            derotation = page.derotation_matrix
                            for finding in ocr_findings:
                                finding.rect = tuple(fitz.Rect(finding.rect) * derotation)
                        findings.extend(ocr_findings)
                    except Exception as exc:
                        warnings.append(PageWarning(n, f'OCR başarısız: {type(exc).__name__}'))
            # A site or map can identify the location even after numbers are removed.
            if large_page or image_area > page_area * .4:
                warnings.append(PageWarning(n, 'Harita/pafta görüntüsü konumu açığa çıkarabilir; görsel inceleme gerekir.'))
            if not words and not imgs:
                warnings.append(PageWarning(n, 'Sayfa metin/görüntü çıkarmaya uygun değil.'))
    finally:
        pdf.close()
    # Different extraction layers can identify the same words. Don't redact twice.
    dedup = {}
    for f in findings:
        r = f.rect
        key = (f.page, f.category, tuple(round(v / 2) for v in r))
        if key not in dedup or f.source == 'PDF metni':
            dedup[key] = f
    return list(dedup.values()), warnings


def verify_text(pdf_path, selected_values):
    """Check exact extracted sensitive strings in the exported PDF."""
    remaining = []
    with fitz.open(str(pdf_path)) as doc:
        all_text = '\n'.join(p.get_text() for p in doc)
        for value in set(selected_values):
            if value and len(value.strip()) > 2 and value.casefold() in all_text.casefold():
                remaining.append(value)
    return remaining


def export_pdf(source, output, findings, manual_rects=None, omitted_pages=None):
    """Save a new cleaned PDF (never overwrite original) and verify extracted text."""
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise ValueError('Kaynak PDF üzerine kaydetme engellendi.')
    if output.exists():
        raise FileExistsError('Hedef dosya zaten var. Yeni dosya adı seçin; üzerine yazılmayacak.')
    manual_rects = manual_rects or {}
    omitted_pages = set(omitted_pages or ())
    by_page = defaultdict(list)
    selected_values = []
    for f in findings:
        if f.selected and f.page not in omitted_pages:
            by_page[f.page].append(f.rect)
            selected_values.append(f.value)
    for n, rects in manual_rects.items():
        if n not in omitted_pages:
            by_page[n].extend(rects)
    if not by_page and not omitted_pages:
        raise ValueError('Hiçbir karartma seçilmedi.')
    doc = fitz.open(str(source))
    try:
        if doc.needs_pass:
            raise ValueError('Şifreli PDF')
        for i in reversed(range(len(doc))):
            if i in omitted_pages:
                doc.delete_page(i)
            else:
                page = doc[i]
                bounds = page.rect * page.derotation_matrix
                for rect in by_page[i]:
                    rr = fitz.Rect(rect) & bounds
                    if not rr.is_empty and not rr.is_infinite:
                        rr = fitz.Rect(rr.x0 - 1, rr.y0 - 1, rr.x1 + 1, rr.y1 + 1) & bounds
                        page.add_redact_annot(rr, fill=(0, 0, 0), cross_out=False)
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                                      graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
        if not len(doc):
            raise ValueError('Tüm sayfalar çıkarılmış; boş PDF oluşturulamaz.')
        doc.scrub(attached_files=True, embedded_files=True, metadata=True, javascript=True,
                  hidden_text=True, xml_metadata=True, thumbnails=True, reset_fields=True)
        doc.set_metadata({})
        doc.save(str(output), garbage=4, deflate=True, clean=True,
                 encryption=fitz.PDF_ENCRYPT_NONE)
    finally:
        doc.close()
    remaining = verify_text(output, selected_values)
    if remaining:
        output.unlink(missing_ok=True)
        raise RuntimeError('Kontrol hatası: bazı metinler çıktı PDF içinde kalmış. Dosya üretilmedi.')
    return str(output)
