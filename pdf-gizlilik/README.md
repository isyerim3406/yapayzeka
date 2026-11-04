# PDF Gizlilik — çevrimdışı pafta ve dosya anonimleştirme

Windows'ta çalışan, kaynak PDF'yi değiştirmeden yeni bir karartılmış PDF hazırlayan masaüstü uygulaması.
İnternet/API/Oracle/Kaggle bağlantısı yoktur; herhangi bir veri yüklenmez, loglanmaz veya otomatik yedeklenmez.

## Kullanım

1. GitHub Actions > **Windows Portable PDF Gizlilik** > son başarılı çalıştırmanın **Artifacts** alanından `PDF-Gizlilik-Windows-Portable.zip` indirin.
2. ZIP dosyasını klasöre çıkarın; `PDF-Gizlilik.exe` dosyasını çalıştırın.
3. **PDF Aç > Tüm Sayfaları Tara** deyin. Firma, koordinat, adres, ada/parsel vb eşleşmeler listelenir.
4. Tekrarlanan bilgilerin birine çift tıklayarak tamamını seçebilir veya seçimi kaldırabilirsiniz. Önizlemede fareyle ilave karartma alanları çizin.
5. Harita ve büyük pafta sayfaları **varsayılan olarak çıktıya alınmaz**; bunlar için tek tek görsel kontrol ve onay gerekir. OCR başarısız sayfalar kesinlikle dışlanır.
6. **Temiz PDF Oluştur** ile yeni dosyayı kaydedin; Gemini'ye göndermeden önce dosyayı gözle inceleyin.

## Güvenlik sınırları

- Bu araç Tamga ile *entegre değildir*; Tamga'daki politika fikrinden yararlanan yerel bir kural motorudur.
- Firma adı, açık adres ve kişi isimleri için düzenli ifadeler kusursuz tanıma sağlamaz. **Ek gizlenecek ifadeler** alanında bilinen unvanları ve şahıs adlarını girin.
- OCR büyük görüntüleri bölerek tarar. Yanlış okunan koordinatları kaçırabilir; OCR taraması yapılamayan sayfalar çıktıdan çıkarılır.
- Harita geometrisi, uydu görüntüsü, yer adları, meta veri veya başka işaretler de konum ifşa edebilir. İnsan kontrolü olmadan gizlilik garantisi verilemez.
- Her sayfa çıktıya dahil edilmez. Özellikle paftalar risk listesinde `DIŞLA` olarak başlar. Bir paftayı onaylamak konumun kesin gizli olduğuna dair teknik güvence değildir.
- Çıktı dosyası kaynak dosyadan farklı olmalıdır, üzerine yazılmaz. Orijinal belge korunur ama **otomatik yedek alınmaz**.
- Çıktıda gömülü dosyalar, JavaScript, metadata ve XML metadata temizlenir. `PyMuPDF` gerçek redaction kullanır.
- Ek gizlenecek ifadeler ve ilk OCR tanıması yalnızca çalıştıran bilgisayarın RAM'inde tutulur.

## Geliştirme

```bash
python -m pip install -r requirements.txt
python app.py
python -m unittest discover -s tests -v
```

Windows EXE ve yerel OCR için GitHub Actions iş akışı Windows runner üzerinde Tesseract'ı ve `tur`+`eng` dil dosyalarını paketler. EXE çıktısı tek dosya değil, ZIP içindeki bağımsız portable klasördür.

## Sürüm

İlk çalışan prototip. Üretim güvenlik onayı için gerçek başvuru dosyalarıyla test ve insan kontrolü gereklidir. PDF'lerdeki geometrik konum verilerini bütünüyle yok etmeyi garanti etmez.
