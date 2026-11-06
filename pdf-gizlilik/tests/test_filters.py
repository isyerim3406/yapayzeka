import json
import io
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from filters import FilterStore, FilterMatcher, LEARNED_CATEGORY, normalize
from engine import scan_pdf,export_pdf
import fitz
from PIL import Image

def line(text,x=30,y=30):
    out=[];a=0
    for word in text.split():
        start=text.index(word,a);end=start+len(word);out.append((start,end,(x,y,x+len(word)*5,y+10)));x+=len(word)*5+4;a=end
    return text,out

class FilterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'filters.json';self.store=FilterStore(self.path)
    def tearDown(self):self.temp.cleanup()
    def test_turkish(self):self.assertEqual(normalize('VERGİ Dairesi'),normalize('Vergi Dairesi'))
    def test_registration_fields(self):
        m=FilterMatcher(self.store.data)
        for text,value in [('Vergi No: 1234567890','1234567890'),('Oda Sicil No: 39178','39178'),('Ticaret Sicil No: 38627','38627'),('MERSIS No: 1234567890123456','1234567890123456')]:
            fs=m.find([line(text)],0,'test');self.assertTrue(any(f.value==value for f in fs),(text,fs))
    def test_columns_and_multiline(self):
        lines=[line('Firma Unvani',30,30),line('ORNEK GERI DONUSUM',150,30),line('Oda Sicil No',400,30),line('39178',500,30),line('SANAYI LIMITED SIRKETI',150,44),line('Vergi No 1234567890',30,58)]
        fs=FilterMatcher(self.store.data).find(lines,0,'test')
        firms=[f for f in fs if f.category=='Firma ve tesis']
        self.assertEqual([f.value for f in firms],['ORNEK GERI DONUSUM','SANAYI LIMITED SIRKETI'])
        self.assertTrue(all(f.rect[2]<400 for f in firms))
        self.assertTrue(any(f.value=='39178' for f in fs))
    def test_disable(self):
        d=self.store.snapshot()
        for r in d['rules']:
            if 'Oda sicil no' in r['aliases']:r['enabled']=False
        fs=FilterMatcher(d).find([line('Oda Sicil No 39178')],0,'test')
        self.assertFalse(fs)
    def test_persist_custom_and_pdf_redact(self):
        d=self.store.snapshot();d['user_rules'].append(dict(id='U1',name='Ozel firma',category='Kullanıcı',kind='literal',enabled=True,aliases=['CUSTOM COMPANY'],max_lines=1,multiline=False,pattern=''))
        self.store.save(d);again=FilterStore(self.path)
        self.assertEqual(again.data,d)
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])
        src=self.path.parent/'a.pdf';out=self.path.parent/'b.pdf'
        doc=fitz.open();p=doc.new_page();p.insert_text((30,50),'CUSTOM COMPANY');p.insert_text((30,90),'Atik kodu: 16 01 03');doc.save(src);doc.close()
        fs,_=scan_pdf(src,use_ocr=False,filter_data=again.data)
        self.assertTrue(any(f.value=='CUSTOM COMPANY' for f in fs))
        export_pdf(src,out,fs)
        with fitz.open(out) as doc:
            self.assertNotIn('CUSTOM COMPANY',doc[0].get_text());self.assertIn('16 01 03',doc[0].get_text())
    def test_no_general_numbers(self):
        fs=FilterMatcher(self.store.data).find([line('NACE 38.32.01 Atik kodu 16 01 03 Kapasite 1234567890')],0,'test')
        self.assertFalse(fs)
    def test_broken_store_not_overwritten(self):
        self.path.write_text('not json');s=FilterStore(self.path);self.assertTrue(s.error);self.assertEqual(self.path.read_text(),'not json')

    def test_remembered_line_prefix_respects_columns_and_disabled_rules(self):
        self.assertEqual(self.store.remember_line_labels(['Referans', 'REFERANS']), ['Referans'])
        second_window = FilterStore(self.path)
        self.assertEqual(second_window.remember_line_labels(['Bölüm 2']), ['Bölüm 2'])
        self.assertEqual(self.store.remember_line_labels(['Dosya Bağlantısı']), ['Dosya Bağlantısı'])
        data = FilterStore(self.path).snapshot()
        self.assertEqual([r['aliases'] for r in data['user_rules']],
                         [['Referans'], ['Bölüm 2'], ['Dosya Bağlantısı']])
        lines = [line('REFERANS :', 30, 30), line('YENI-9274', 160, 30), line('Durum: UYGUN', 320, 30),
                 line('Not metninde Referans: YENI-9274', 30, 55),
                 line('Referans Numarası: KALSIN', 30, 80), line('BÖLÜM 2: ALFA', 30, 105)]
        found = [f for f in FilterMatcher(data).find(lines, 0, 'test') if f.line_prefix]
        self.assertEqual([f.value for f in found], ['REFERANS : YENI-9274', 'BÖLÜM 2: ALFA'])
        self.assertLess(found[0].rect[2], 320)
        data['user_rules'][0]['enabled'] = False
        self.store.save(data)
        self.assertEqual(self.store.remember_line_labels(['Referans']), [])
        self.assertEqual([f.value for f in FilterMatcher(self.store.data).find(lines, 0, 'test') if f.line_prefix],
                         ['BÖLÜM 2: ALFA'])

    def test_ocr_heading_coordinates_are_mapped_back_to_rotated_pdf(self):
        source = self.path.parent / 'image.pdf'
        self.store.remember_line_labels(['Referans'])
        text, boxes = line('Referans: YENI-87')
        image = io.BytesIO()
        Image.new('RGB', (150, 200), 'white').save(image, format='PNG')
        with fitz.open() as doc:
            page = doc.new_page(width=300, height=400)
            page.insert_image(page.rect, stream=image.getvalue())
            page.set_rotation(90)
            expected = tuple(fitz.Rect(boxes[0][2]) * page.derotation_matrix)
            doc.save(source)
        label_hits = {}
        with (patch('engine.ocr_available', return_value=True),
              patch('engine._ocr_lines', return_value=[(text, boxes)])):
            found, _ = scan_pdf(source, filter_data=self.store.snapshot(), label_hits=label_hits)
        self.assertEqual(label_hits[0][0][0], 'Referans')
        self.assertEqual(label_hits[0][0][1][0], expected)
        self.assertTrue(any(f.category == LEARNED_CATEGORY and f.line_prefix for f in found))

if __name__=='__main__':unittest.main()
