import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import fitz
from PIL import ImageChops, ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filters import FilterStore
from finereader import OcrSettings


@unittest.skipUnless(sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI display unavailable')
class ManualRedactionTests(unittest.TestCase):
    def test_drag_and_export_after_rotating_zooming_and_scrolling(self):
        from app import Application

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, output = folder / 'source.pdf', folder / 'redacted.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=800, height=400)
                page.draw_rect(fitz.Rect(616, 89, 667, 119), color=None, fill=(1, 0, 0))
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
                    app.rotate_view(90)
                    for _ in range(5):
                        app.zoom_in_button.invoke()
                    self.assertGreater(int(app.zoom_text.get().lstrip('%')), 100)
                    # Locate the target in the actual preview bitmap instead of using the app's transform.
                    red, green, _ = ImageTk.getimage(app.show_image).convert('RGB').split()
                    mask = ImageChops.multiply(red.point(lambda n: 255 if n > 240 else 0),
                                               green.point(lambda n: 255 if n < 12 else 0))
                    image_left, image_top, _, _ = app.canvas.bbox('page-image')
                    x0, y0, x1, y1 = mask.getbbox()
                    region = [float(n) for n in str(app.canvas.cget('scrollregion')).split()]
                    center_x = image_left + (x0 + x1) / 2
                    center_y = image_top + (y0 + y1) / 2
                    app.canvas.xview_moveto((center_x - app.canvas.winfo_width() / 2) / region[2])
                    app.canvas.yview_moveto((center_y - app.canvas.winfo_height() / 2) / region[3])
                    scroll_x, scroll_y = app.canvas.canvasx(0), app.canvas.canvasy(0)
                    self.assertTrue(scroll_x > 0 or scroll_y > 0)
                    left = math.floor(image_left + x0 - scroll_x - 2)
                    top = math.floor(image_top + y0 - scroll_y - 2)
                    right = math.ceil(image_left + x1 - scroll_x + 2)
                    bottom = math.ceil(image_top + y1 - scroll_y + 2)
                    app.canvas.event_generate('<ButtonPress-1>', x=left, y=top)
                    app.canvas.event_generate('<B1-Motion>', x=right, y=bottom)
                    app.canvas.event_generate('<ButtonRelease-1>', x=right, y=bottom)
                    self.assertEqual(len(app.manual[0]), 1)
                    self.assertTrue(app.export_button.instate(['!disabled']))
                    marked = app.manual[0][0]
                    app.zoom_out_button.invoke()
                    app.fit_view()
                    self.assertEqual(app.zoom_text.get(), '%100')
                    self.assertEqual(app.manual[0], [marked])
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
                        self.assertEqual(doc[0].rotation, 90)
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
