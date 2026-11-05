"""Local FineReader PDF bridge using its documented command-line interface.

FineReader remains in the user's own installation / portable directory. No
ABBYY files, license keys, or private DLL interfaces are redistributed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

import fitz
from PIL import Image, ImageDraw, ImageFont

from filters import user_path


ENGINES = ('Tesseract', 'FineReader')
TEST_PHRASE = 'OCR TEST 482719'


class OcrCancelled(InterruptedError):
    pass


class OcrSettings:
    def __init__(self, path=None):
        self.path = Path(path) if path else user_path().with_name('ocr_settings.json')
        self.data = {'engine': 'Tesseract', 'finereader_exe': ''}
        self.error = None
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or data.get('engine') not in ENGINES or not isinstance(data.get('finereader_exe'), str):
                    raise ValueError('OCR ayar dosyası geçersiz.')
                self.data.update({k: data[k] for k in self.data})
            except (OSError, ValueError) as exc:
                self.error = str(exc)

    def save(self, engine, exe):
        if engine not in ENGINES:
            raise ValueError('OCR motoru geçersiz.')
        data = {'engine': engine, 'finereader_exe': str(exe)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.ocr-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(data, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.data = data
        self.error = None


def validate_executable(filename):
    exe = Path(filename).expanduser().resolve()
    if not exe.is_file() or exe.name.lower() != 'finereaderocr.exe':
        raise ValueError('Tam FineReader klasöründeki FineReaderOCR.exe dosyasını seçin.')
    with exe.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Seçilen dosya bir Windows uygulaması değil.')
    # These direct dependencies were verified in the supplied version's PE imports.
    present = {p.name.lower() for p in exe.parent.iterdir() if p.is_file()}
    missing = [name for name in ('fineobj.dll', 'awl.dll', 'langinfounicode.dll') if name not in present]
    if missing:
        raise ValueError('FineReader klasörü eksik: ' + ', '.join(missing) +
                         '. Ayrı kopyalanmış EXE yerine çalışan tam klasörü seçin.')
    return exe


def command_args(exe, source, output):
    return [str(exe), str(source), '/lang', 'Turkish', 'English', '/out', str(output)]


@dataclass
class OcrDocument:
    path: Path
    directory: Path
    pages: int
    words: int

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def _read_completed_pdf(path, expected_pages):
    """Snapshot a complete file. Never use a PDF still being written for redaction."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:
        return None
    try:
        with fitz.open(stream=data, filetype='pdf') as doc:
            if doc.needs_pass or doc.is_repaired:
                return None
            if len(doc) != expected_pages:
                # A valid multi-page result can still be an intermediate save.
                return None
            words = sum(len(page.get_text('words')) for page in doc)
    except Exception:
        return None
    if not words:
        raise RuntimeError('FineReader PDF üretti ancak okunabilir metin katmanı yok. '
                           'FineReader’da aranabilir PDF çıktısını seçin.')
    return data, words


def _cleanup(directory, process):
    """Do not terminate a user's FineReader UI on cancellation or on an error."""
    shutil.rmtree(directory, ignore_errors=True)
    if directory.exists() and process is not None:
        def later():
            try:
                process.wait(timeout=300)
            except Exception:
                pass
            shutil.rmtree(directory, ignore_errors=True)
        threading.Thread(target=later, daemon=True).start()


def convert_document(executable, source, *, cancel=None, progress=None,
                     timeout=1200, process_factory=None, stable_seconds=2.0):
    """Return a temporary searchable PDF, to be previewed AND redacted directly.

    Source-page coordinates are deliberately not transferred across an OCR
    conversion: FineReader can rotate or crop pages. The returned snapshot is
    the sole working document for both display and subsequent export.
    """
    exe = validate_executable(executable)
    source = Path(source).resolve()
    if cancel is not None and cancel.is_set():
        raise OcrCancelled('İşlem iptal edildi.')
    with fitz.open(source) as doc:
        if doc.needs_pass or not doc.is_pdf or not len(doc):
            raise ValueError('Kaynak, şifresiz ve en az bir sayfalı PDF olmalı.')
        expected_pages = len(doc)
    directory = Path(tempfile.mkdtemp(prefix='PDF-Gizlilik-OCR-'))
    output = directory / 'finereader_output.pdf'
    process = None
    try:
        factory = process_factory or subprocess.Popen
        try:
            process = factory(command_args(exe, source, output), cwd=str(exe.parent),
                              shell=False, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise RuntimeError('FineReader başlatılamadı. Tam klasörden normal açıldığını kontrol edin. '
                               f'Windows hata kodu: {getattr(exc, "winerror", None) or exc.errno}') from exc
        started = time.monotonic()
        last_state, unchanged_since = None, None
        while True:
            if cancel is not None and cancel.is_set():
                raise OcrCancelled('İşlem iptal edildi. FineReader penceresi açık kalabilir.')
            elapsed = time.monotonic() - started
            if progress:
                progress(int(elapsed), timeout, 'FineReader OCR sonucu bekleniyor')
            try:
                stat = output.stat()
                state = (stat.st_size, stat.st_mtime_ns)
                if state != last_state:
                    last_state, unchanged_since = state, time.monotonic()
                if stat.st_size and time.monotonic() - unchanged_since >= stable_seconds:
                    result = _read_completed_pdf(output, expected_pages)
                    if result is not None:
                        data, words = result
                        snapshot = directory / 'working.pdf'
                        snapshot.write_bytes(data)
                        return OcrDocument(snapshot, directory, expected_pages, words)
            except FileNotFoundError:
                pass
            code = process.poll()
            if code not in (None, 0):
                raise RuntimeError(f'FineReader hata kodu {code} ile kapandı. '
                                   'FineReader penceresindeki hata mesajını kontrol edin.')
            if elapsed >= timeout:
                raise TimeoutError('FineReader’dan tamamlanmış ve metin katmanı içeren PDF alınamadı. '
                                   'FineReader penceresindeki hata veya lisans mesajını kontrol edin. '
                                   'Otomatik /out kaydı için Extended CLI desteği gerekebilir.')
            if cancel is not None:
                cancel.wait(.4)
            else:
                time.sleep(.4)
    except BaseException:
        _cleanup(directory, process)
        raise


def _sample_pdf(filename):
    """Image-only input ensures the connection test really exercises OCR."""
    image = Image.new('RGB', (1600, 500), 'white')
    font = None
    for candidate in ('C:/Windows/Fonts/arial.ttf',
                      '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            font = ImageFont.truetype(candidate, 68)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default(size=68)
    ImageDraw.Draw(image).text((90, 170), TEST_PHRASE, font=font, fill='black')
    import io
    data = io.BytesIO()
    image.save(data, format='PNG')
    with fitz.open() as doc:
        page = doc.new_page(width=800, height=250)
        page.insert_image(page.rect, stream=data.getvalue())
        doc.save(filename)


def test_connection(executable, *, cancel=None, progress=None, **kwargs):
    with tempfile.TemporaryDirectory(prefix='PDF-Gizlilik-Deneme-', ignore_cleanup_errors=True) as folder:
        sample = Path(folder) / 'ocr_test.pdf'
        _sample_pdf(sample)
        result = convert_document(executable, sample, cancel=cancel, progress=progress,
                                  timeout=120, **kwargs)
        try:
            with fitz.open(result.path) as doc:
                text = ' '.join(page.get_text() for page in doc)
            if '482719' not in text:
                raise RuntimeError('FineReader çalıştı ancak deneme sayfasındaki metin doğrulanamadı.')
            return 'FineReader bağlantısı çalışıyor. Görüntülü deneme sayfası tanındı ve otomatik PDF çıktısı doğrulandı.'
        finally:
            result.cleanup()
