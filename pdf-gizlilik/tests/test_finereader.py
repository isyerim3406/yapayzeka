import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finereader import (OcrCancelled, OcrSettings, convert_document, command_args,
                        validate_executable, test_connection)
from filters import FilterStore
from engine import scan_pdf, export_pdf


def make_pdf(filename, text='Vergi No: 1234567890', *, width=600, height=800,
             count=1, image=False):
    with fitz.open() as doc:
        for _ in range(count):
            page = doc.new_page(width=width, height=height)
            if image:
                from PIL import Image
                import io
                buf = io.BytesIO()
                Image.new('RGB', (60, 80), 'white').save(buf, format='PNG')
                page.insert_image(page.rect, stream=buf.getvalue())
            if text:
                page.insert_text((50, 90), text, fontsize=14)
        doc.save(filename)


class Process:
    def __init__(self, code=0): self.code=code
    def poll(self): return self.code
    def wait(self, **kwargs): return self.code


class FineReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.exe = self.folder / 'FineReaderOCR.exe'
        self.exe.write_bytes(b'MZtest')
        for name in ('FineObj.dll', 'Awl.dll', 'LangInfoUnicode.dll'):
            (self.folder/name).write_bytes(b'fake')
        self.source = self.folder/'original.pdf'
        make_pdf(self.source, image=True)
        self.original = self.source.read_bytes()

    def tearDown(self): self.temp.cleanup()

    def runner(self, text='Vergi No: 1234567890', **pdf_options):
        def fake(args, **kwargs):
            self.assertFalse(kwargs['shell'])
            self.assertEqual(kwargs['cwd'], str(self.folder))
            self.assertEqual(args[:6], [str(self.exe), str(self.source), '/lang', 'Turkish', 'English', '/out'])
            make_pdf(args[-1], text, **pdf_options)
            return Process()
        return fake

    def test_auto_conversion_then_filter_then_redact_same_working_pdf(self):
        # Simulate FineReader rotating/reformatting the page. Use the converted
        # document's coordinates for BOTH preview and export, never the source.
        result = convert_document(self.exe,self.source, process_factory=self.runner(width=800,height=600),stable_seconds=0)
        try:
            fs,warnings=scan_pdf(result.path, use_ocr=False, external_ocr=True,
                                  filter_data=FilterStore(self.folder/'rules.json').data)
            self.assertTrue(any('1234567890' in f.value for f in fs))
            self.assertTrue(all(f.source.startswith('FineReader OCR') for f in fs))
            out=self.folder/'anonymous.pdf'
            export_pdf(result.path,out,fs)
            with fitz.open(out) as doc:
                self.assertEqual(doc[0].rect.width,800)
                self.assertNotIn('1234567890',doc[0].get_text())
            self.assertEqual(self.original,self.source.read_bytes())
        finally: result.cleanup()
        self.assertFalse(result.directory.exists())

    def test_no_text_layer_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'metin katmanı yok'):
            convert_document(self.exe,self.source,process_factory=self.runner(text='',image=True),stable_seconds=0)

    def test_wrong_page_count_never_accepted(self):
        with self.assertRaises(TimeoutError):
            convert_document(self.exe,self.source,process_factory=self.runner(count=2),stable_seconds=0,timeout=0)

    def test_nonzero_exit(self):
        with self.assertRaisesRegex(RuntimeError,'hata kodu 7'):
            convert_document(self.exe,self.source,process_factory=lambda *a,**k:Process(7))

    def test_no_output_timeout(self):
        with self.assertRaisesRegex(TimeoutError,'Extended CLI'):
            convert_document(self.exe,self.source,process_factory=lambda *a,**k:Process(),timeout=0)

    def test_cancellation_before_and_after_launch(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(OcrCancelled):
            convert_document(self.exe,self.source,cancel=cancel)
        cancel.clear()
        def cancel_runner(*a,**k):
            cancel.set();return Process()
        with self.assertRaises(OcrCancelled):
            convert_document(self.exe,self.source,cancel=cancel,process_factory=cancel_runner)

    def test_missing_runtime_files(self):
        (self.folder/'FineObj.dll').unlink()
        with self.assertRaisesRegex(ValueError,'Tam|tam'):
            validate_executable(self.exe)

    def test_settings_persist_without_backups(self):
        settings_path=self.folder/'settings'/'ocr.json'
        settings=OcrSettings(settings_path);settings.save('FineReader',self.exe)
        self.assertEqual(OcrSettings(settings_path).data['finereader_exe'],str(self.exe))
        self.assertEqual(list(settings_path.parent.iterdir()),[settings_path])

    def test_connection_uses_image_only_synthetic_input(self):
        def fake(args, **kwargs):
            with fitz.open(args[1]) as doc:
                self.assertEqual(doc[0].get_text(),'')
                self.assertTrue(doc[0].get_image_info())
            make_pdf(args[-1],text='OCR TEST 482719',width=800,height=250)
            return Process()
        result=test_connection(self.exe,process_factory=fake,stable_seconds=0)
        self.assertIn('doğrulandı',result)

    def test_external_ocr_without_tesseract_and_without_text_is_blocked(self):
        source=self.folder/'no_text.pdf';make_pdf(source,text='',image=True)
        with patch('engine._ocr_lines',side_effect=AssertionError('Tesseract must not run')):
            fs,warnings=scan_pdf(source,use_ocr=False,external_ocr=True)
        self.assertTrue(any('FineReader OCR' in w.reason for w in warnings))


@unittest.skipUnless(sys.platform=='win32' or os.environ.get('DISPLAY'),'GUI display unavailable')
class AppWorkflowTests(unittest.TestCase):
    def test_scan_automatically_converts_then_filters(self):
        from app import Application
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);settings=OcrSettings(folder/'ocr.json')
            exe=folder/'FineReaderOCR.exe';exe.write_bytes(b'MZ')
            for name in ('FineObj.dll','Awl.dll','LangInfoUnicode.dll'):(folder/name).touch()
            settings.save('FineReader',exe)
            source=folder/'source.pdf';make_pdf(source,image=True)
            result_file=folder/'working.pdf';make_pdf(result_file,'Vergi No: 1234567890',width=800,height=600)
            from finereader import OcrDocument
            result=OcrDocument(result_file,folder/'disposable',1,3)
            with (patch('app.OcrSettings',return_value=settings),
                  patch('app.FilterStore',return_value=FilterStore(folder/'rules.json')),
                  patch('app.convert_document',return_value=result) as convert):
                root=Application();root.withdraw()
                try:
                    root.original_path=root.path=str(source);root.page_count=1
                    root.start_scan()
                    import time
                    deadline=time.monotonic()+10
                    while root.busy and time.monotonic()<deadline:
                        root.update();time.sleep(.01)
                    self.assertFalse(root.busy)
                    convert.assert_called_once()
                    self.assertEqual(root.path,str(result_file))
                    self.assertTrue(any('1234567890' in f.value for f in root.findings))
                    # A subsequent filter scan reuses the OCR result automatically.
                    root.start_scan()
                    while root.busy and time.monotonic()<deadline:
                        root.update();time.sleep(.01)
                    self.assertEqual(convert.call_count,1)
                finally:root.close_app()
