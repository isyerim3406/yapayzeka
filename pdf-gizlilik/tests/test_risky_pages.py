import gc
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import PageWarning
from filters import FilterStore
from finereader import OcrSettings


@unittest.skipUnless(sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI display unavailable')
class RiskyPagesTests(unittest.TestCase):
    def setUp(self):
        # Dispose of previous tests' Tk objects before starting a worker thread.
        gc.collect()

    def wait_for_idle(self, app):
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        self.assertFalse(app.busy)

    def test_batch_approval_and_retry_only_excluded_pages(self):
        from app import Application

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / 'source.pdf'
            image = io.BytesIO()
            Image.new('RGB', (120, 160), 'white').save(image, format='PNG')
            with fitz.open() as doc:
                for _ in range(5):
                    page = doc.new_page(width=600, height=800)
                    page.insert_image(page.rect, stream=image.getvalue())
                doc.save(source)

            scanned = []

            def recognize(page, *, zoom):
                scanned.append((page.number, zoom))
                if page.number == 3:
                    return [('X: 442350', [(0, 9, (20, 30, 100, 45))])]
                return []

            settings = OcrSettings(folder / 'ocr.json')
            settings.data['engine'] = 'FineReader'
            with (patch('app.OcrSettings', return_value=settings),
                  patch('app.FilterStore', return_value=FilterStore(folder / 'rules.json')),
                  patch('app.filedialog.askopenfilename', return_value=str(source)),
                  patch('app.ocr_available', return_value=True),
                  patch('engine.ocr_available', return_value=True),
                  patch('engine._ocr_lines', side_effect=recognize),
                  patch('app.messagebox.askyesno', return_value=True) as confirm,
                  patch('app.messagebox.showerror') as showerror):
                app = Application()
                try:
                    app.update()
                    app.open_file()
                    app.warnings = [PageWarning(n, 'Harita/pafta, görsel inceleme gerekir.') for n in range(3)]
                    app.warnings += [PageWarning(n, 'FineReader OCR metin katmanı alınamadı.') for n in (3, 4)]
                    app._update_lists()
                    self.assertEqual(str(app.risks.cget('selectmode')), 'extended')
                    app.risks.selection_set('risk0', 'risk1', 'risk3')
                    app.update()
                    app.approve_selected_button.invoke()
                    self.assertEqual(app.omit_pages, {2, 3, 4})
                    self.assertEqual(confirm.call_count, 1)
                    app.approve_all_button.invoke()
                    self.assertEqual(app.omit_pages, {3, 4})
                    self.assertTrue(app.approve_all_button.instate(['disabled']))
                    self.assertEqual(confirm.call_count, 2)

                    app.retry_button.invoke()
                    self.wait_for_idle(app)
                    showerror.assert_not_called()
                    self.assertEqual([page for page, _ in scanned], [3, 4])
                    self.assertTrue(all(zoom > 2.2 for _, zoom in scanned))
                    self.assertEqual(app.approved_pages, {0, 1, 2})
                    self.assertEqual(app.manual_required, {4})
                    self.assertIn('4 · DIŞLA', app.risks.set('risk3', 'state'))
                    self.assertTrue(any(f.page == 3 and '442350' in f.value for f in app.findings))
                    app.approve_all_button.invoke()
                    self.assertEqual(app.omit_pages, {4})
                finally:
                    app.close_app()

    def test_manual_review_keeps_all_32_pages_in_export(self):
        from app import Application

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, output = folder / 'source.pdf', folder / 'redacted.pdf'
            risky = set(range(0, 32, 2)) | {29, 31}
            sensitive = fitz.Rect(36, 90, 134, 130)
            manual_mark = fitz.Rect(32, 86, 138, 134)
            image = io.BytesIO()
            Image.new('RGB', (98, 40), 'red').save(image, format='PNG')
            with fitz.open() as doc:
                for page_no in range(32):
                    page = doc.new_page(width=300, height=420)
                    page.insert_text((36, 50), f'KALSIN {page_no + 1:02d}', fontsize=12)
                    if page_no in risky:
                        page.insert_image(sensitive, stream=image.getvalue())
                        page.insert_text((42, 115), 'GIZLI', fontsize=12)
                doc.save(source)

            with (patch('app.OcrSettings', return_value=OcrSettings(folder / 'ocr.json')),
                  patch('app.FilterStore', return_value=FilterStore(folder / 'rules.json')),
                  patch('app.filedialog.askopenfilename', return_value=str(source)),
                  patch('app.filedialog.asksaveasfilename', return_value=str(output)),
                  patch('app.ocr_available', return_value=True),
                  patch('engine.ocr_available', return_value=True),
                  patch('engine._ocr_lines', side_effect=RuntimeError),
                  patch('app.messagebox.askyesno', return_value=True) as confirm,
                  patch('app.messagebox.showinfo') as info,
                  patch('app.messagebox.showerror') as showerror):
                app = Application()
                try:
                    app.update()
                    app.open_file()
                    app.start_scan()
                    self.wait_for_idle(app)
                    self.assertEqual(app.omit_pages, risky)
                    self.assertIn('14 / 32', app.warning_total.get())
                    self.assertTrue(app.approve_all_button.instate(['disabled']))
                    self.assertEqual(app.risks.set('risk0', 'state'), '1 · ELLE KARARTIN')

                    app.risks.selection_set('risk0')
                    app.update()
                    app.goto(1)
                    for page_no in sorted(risky):
                        row = f'risk{page_no}'
                        app.risks.see(row)
                        app.update()
                        x, y, _, height = app.risks.bbox(row)
                        app.risks.event_generate('<ButtonRelease-1>', x=x + 6, y=y + height // 2)
                        self.assertEqual(app.page_no, page_no)
                        self.assertTrue(app.canvas.find_withtag('page-image'))
                        x0, y0, x1, y1 = (manual_mark * app.pdf_to_preview).irect
                        app.canvas.event_generate('<ButtonPress-1>', x=x0, y=y0)
                        app.canvas.event_generate('<B1-Motion>', x=x1, y=y1)
                        app.canvas.event_generate('<ButtonRelease-1>', x=x1, y=y1)
                        self.assertIn('ONAY BEKLİYOR', app.risks.set(row, 'state'))
                        self.assertIn(page_no, app.omit_pages)
                        if page_no == 0:
                            app.approve_selected_button.invoke()
                            self.assertEqual(app.risks.set(row, 'state'), '1 · ELLE ONAYLI')
                    app.approve_all_button.invoke()
                    self.assertEqual(app.approved_pages, risky)
                    self.assertEqual(app.omit_pages, set())
                    self.assertIn('32 / 32', app.warning_total.get())

                    app.start_scan()
                    self.wait_for_idle(app)
                    self.assertEqual(app.approved_pages, risky)
                    self.assertEqual(app.omit_pages, set())
                    app.export_button.invoke()
                    self.wait_for_idle(app)
                    showerror.assert_not_called()
                    self.assertIn('32 / 32', confirm.call_args.args[1])
                    self.assertIn('32 sayfalık PDF kaydedildi', info.call_args.args[1])
                    with fitz.open(output) as doc:
                        self.assertEqual(len(doc), 32)
                        for page_no, page in enumerate(doc):
                            self.assertEqual(page.get_text().strip(), f'KALSIN {page_no + 1:02d}')
                            if page_no in risky:
                                self.assertEqual(page.get_pixmap().pixel(75, 105), (0, 0, 0))

                    app.goto(0)
                    app.clear_manual()
                    self.assertEqual(app.omit_pages, {0})
                    self.assertEqual(app.risks.set('risk0', 'state'), '1 · ELLE KARARTIN')
                    self.assertIn('31 / 32', app.warning_total.get())
                finally:
                    app.close_app()
