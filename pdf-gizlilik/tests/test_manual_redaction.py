import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filters import FilterStore
from finereader import OcrSettings


@unittest.skipUnless(sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI display unavailable')
class ManualRedactionTests(unittest.TestCase):
    def test_drag_and_export_rotated_pdf_without_automatic_scan(self):
        from app import Application

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, output = folder / 'source.pdf', folder / 'redacted.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=800, height=400)
                page.insert_text((620, 110), 'GIZLI', fontsize=16)
                page.insert_text((45, 55), 'KALSIN', fontsize=16)
                page.set_rotation(90)
                visible_mark = page.search_for('GIZLI')[0] * page.rotation_matrix
                doc.save(source)
            original = source.read_bytes()

            with (patch('app.OcrSettings', return_value=OcrSettings(folder / 'ocr.json')),
                  patch('app.FilterStore', return_value=FilterStore(folder / 'rules.json')),
                  patch('app.filedialog.askopenfilename', return_value=str(source)),
                  patch('app.filedialog.asksaveasfilename', return_value=str(output)),
                  patch('app.messagebox.askyesno', return_value=True),
                  patch('app.messagebox.showinfo'),
                  patch('app.messagebox.showerror') as showerror,
                  patch('app.scan_pdf') as scan):
                app = Application()
                try:
                    app.update()
                    app.open_file()
                    app.update()
                    self.assertTrue(app.export_button.instate(['disabled']))
                    left = math.floor(app.offset_x + (visible_mark.x0 - 2) * app.scale)
                    top = math.floor(app.offset_y + (visible_mark.y0 - 2) * app.scale)
                    right = math.ceil(app.offset_x + (visible_mark.x1 + 2) * app.scale)
                    bottom = math.ceil(app.offset_y + (visible_mark.y1 + 2) * app.scale)
                    app.canvas.event_generate('<ButtonPress-1>', x=left, y=top)
                    app.canvas.event_generate('<B1-Motion>', x=right, y=bottom)
                    app.canvas.event_generate('<ButtonRelease-1>', x=right, y=bottom)
                    self.assertEqual(len(app.manual[0]), 1)
                    self.assertTrue(app.export_button.instate(['!disabled']))
                    app.export_button.invoke()
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.01)
                    self.assertFalse(app.busy)
                    showerror.assert_not_called()
                    scan.assert_not_called()
                    with fitz.open(output) as doc:
                        self.assertEqual(len(doc), 1)
                        text = doc[0].get_text()
                        self.assertNotIn('GIZLI', text)
                        self.assertIn('KALSIN', text)
                        cx = int((visible_mark.x0 + visible_mark.x1) / 2)
                        cy = int((visible_mark.y0 + visible_mark.y1) / 2)
                        self.assertEqual(doc[0].get_pixmap().pixel(cx, cy), (0, 0, 0))
                    self.assertEqual(source.read_bytes(), original)
                    app.clear_manual()
                    self.assertTrue(app.export_button.instate(['disabled']))
                finally:
                    app.close_app()
