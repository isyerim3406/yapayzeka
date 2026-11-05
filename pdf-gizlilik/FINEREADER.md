# FineReader OCR modülü

PDF Gizlilik v3, bilgisayarınızdaki tam FineReader PDF klasörünü OCR sağlayıcısı olarak çağırır. ABBYY DLL’lerini uygulamaya kopyalamanız gerekmez. Gönderilen altı dosyanın statik incelemesinde dışa açık bağımsız bir OCR SDK girişi bulunmadığından, belgelenmiş FineReaderOCR.exe komut satırı kullanılır.

## İlk kullanım

1. ZIP’i çıkarıp PDF-Gizlilik.exe dosyasını çalıştırın.
2. FineReader Konumu düğmesinden mevcut, normal çalışan FineReader klasörünüzdeki FineReaderOCR.exe dosyasını seçin. Konum bir kez kaydedilir.
3. Bağlantıyı Dene düğmesiyle görüntülü örnek sayfanın OCR’den geçirilmesini ve otomatik PDF çıktısını doğrulayın. Bu denemede kişisel PDF’niz kullanılmaz.
4. PDF Aç → Tüm Sayfaları Tara.

FineReader, belgeyi OCR’den geçirir. Uygulama tamamlanmış PDF çıktısını otomatik bekler; sayfa sayısını ve metin katmanını denetler; çıktı hazır olunca 128 filtreyle taramayı kendisi başlatır. Önizleme ve karartma aynı OCR çalışma PDF’sine uygulanır. FineReader sayfayı döndürse veya yeniden boyutlandırsa bile orijinal PDF’nin koordinatları farklı belgeye taşınmaz.

Aynı PDF’de yalnız filtreler değişirse alınan OCR sonucu tekrar kullanılır; FineReader yeniden çalıştırılmaz. Kaynak PDF değişirse OCR yeniden yapılır. OCR motoru değiştiğinde el ile çizilmiş alanların yeniden çizilmesi gerekirse uygulama bunu açıklar. İptal düğmesi beklemeyi durdurur; FineReader’ın kendi penceresi açık kalabilir.

## Gerçek çalışma koşulu

Otomatik kaydetme FineReader’ın /out komutunu kullanır. ABBYY’nin belgelerine göre bu özellik Extended CLI desteği gerektirebilir. Dosyaların bulunması desteğin etkin olduğunu kanıtlamaz. FineReader bir hata/lisans penceresi gösterirse mesajı kontrol edin. Uygulama başarı doğrulanmadan filtrelemeye geçmez ve FineReader seçiliyken sessizce Tesseract’a dönmez.

FineReader masaüstü sürümü kendi penceresini gösterebilir. Bu bağlantı headless Engine SDK değildir. Tam FineReader kurulumu/portable klasörü ve onun çalışan ortamı gerekir; yalnızca incelemeye gönderilen altı dosya yeterli değildir.

## Dosyalar ve test kapsamı

OCR sonucu, yalnız bu iş için oluşturulan geçici çalışma klasöründe tutulur ve belge kapatılırken/uygulamadan çıkarken temizlenir. Ayarlara yalnız motor seçimi ve FineReader program yolu yazılır. Otomatik belge yedeği oluşturulmaz.

Testler: otomatik sonuç alma → filtreleme → aynı çalışma belgesini karartma, farklı sayfa boyutu, sayfa sayısı uyuşmazlığı, eksik metin katmanı, başarısız başlatma/çıkış, iptal, kalıcı ayarlar ve Windows arayüz akışı. Otomasyon sözleşmesi taklit FineReader süreciyle test edilir. Kullanıcının gerçek FineReader 16 kurulumunun /out desteği bu ortamda çalıştırılmadı; Bağlantıyı Dene ile yerelde doğrulanır.

Resmî başvuru:
https://help.abbyy.com/en-us/finereader/16/user_guide/commandline_save/
https://help.abbyy.com/en-us/finereader/16/user_guide/commandline_convert/
