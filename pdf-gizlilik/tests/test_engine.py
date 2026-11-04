import os
from pathlib import Path
import sys
import tempfile
import unittest

import fitz
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import scan_pdf, export_pdf, _tckn, ocr_available


class EngineTests(unittest.TestCase):
    def test_tckn(self):
        self.assertTrue(_tckn('10000000146'))
        self.assertFalse(_tckn('10000000147'))

    def test_pdf_redaction_and_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            a = Path(temp) / 'source.pdf'
            b = Path(temp) / 'clean.pdf'
            d = fitz.open()
            p = d.new_page()
            p.insert_text((40, 70), 'X: 442350 Y: 4432150', fontsize=13)
            p.insert_text((40, 100), '125 Ada 8 Parsel', fontsize=13)
            p.insert_text((40, 130), 'Gizli Kurulus Ltd. Sti.', fontsize=13)
            p.insert_text((40, 160), 'Taban drenaji 30 cm kil ile korunur.', fontsize=13)
            d.set_metadata({'title': 'Confidential project coordinates'})
            d.save(a)
            d.close()
            f, warnings = scan_pdf(a, use_ocr=False)
            self.assertGreaterEqual(len([x for x in f if x.category == 'Koordinat']), 2)
            self.assertTrue(any(x.category == 'Ada/Parsel' for x in f))
            self.assertFalse(warnings)
            export_pdf(a, b, f)
            with fitz.open(b) as out:
                txt = ''.join(p.get_text() for p in out)
                self.assertNotIn('442350', txt)
                self.assertNotIn('4432150', txt)
                self.assertNotIn('125 Ada', txt)
                self.assertIn('Taban drenaji', txt)
                self.assertNotIn('Confidential', str(out.metadata))
            with self.assertRaises(FileExistsError):
                export_pdf(a, b, f)

    @unittest.skipUnless(ocr_available(), 'Tesseract OCR missing')
    def test_image_only_map_ocr(self):
        with tempfile.TemporaryDirectory() as temp:
            a = Path(temp) / 'map.pdf'
            img = Image.new('RGB', (1500, 1000), 'white')
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 46)
            except OSError:
                font = ImageFont.load_default()
            draw.text((90, 140), 'X: 442350', fill='black', font=font)
            draw.text((90, 250), 'Y: 4432150', fill='black', font=font)
            draw.line((90, 400, 1320, 760), fill='black', width=8)
            imgpath = Path(temp) / 'drawing.png'
            img.save(imgpath)
            doc = fitz.open()
            page = doc.new_page(width=900, height=600)
            page.insert_image(page.rect, filename=str(imgpath))
            doc.save(a)
            doc.close()
            findings, warnings = scan_pdf(a, use_ocr=True)
            self.assertTrue(any(x.category == 'Koordinat' for x in findings),
                            [(x.category, x.value) for x in findings])
            self.assertTrue(any('Harita' in x.reason for x in warnings))


if __name__ == '__main__':
    unittest.main()
