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
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.01)
                    self.assertFalse(app.busy)
                    showerror.assert_not_called()
                    self.assertEqual([page for page, _ in scanned], [3, 4])
                    self.assertTrue(all(zoom > 2.2 for _, zoom in scanned))
                    self.assertEqual(app.approved_pages, {0, 1, 2})
                    self.assertEqual(app.mandatory_omit, {4})
                    self.assertIn('4 · DIŞLA', app.risks.set('risk3', 'state'))
                    self.assertTrue(any(f.page == 3 and '442350' in f.value for f in app.findings))
                    app.approve_all_button.invoke()
                    self.assertEqual(app.omit_pages, {4})
                finally:
                    app.close_app()
