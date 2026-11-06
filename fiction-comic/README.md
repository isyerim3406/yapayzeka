# Fiction Studio – Comic Book eklentisi

Mevcut Oracle `/home/ubuntu/fiction-gpu` web paneline **Comic Book Hazırla** butonu ekler.

**Girdi seçenekleri**
- Cydonia tarafından son oluşturulan asistan mesajı (metin kutusu boşken)
- Metin kutusuna yapıştırılan hikâye
- Her satıra bir adet hikâye URL'si
- Tek bağlantının altındaki numaralı sayfaların (`1 2 3…`) sırayla toplanması

**Otomatik işlem:** herkese açık HTML metnini indir → Cydonia JSON panellere böler → RealVisXL 768×768/20 adım fotogerçekçi sahneler üretir → Oracle Türkçe konuşmaları görsel altı baloncuklara yerleştirir → mobil PDF → **Biten İşler / PDF Aç**. Telegram ayarlanmışsa yalnızca son başarılı tamamlanma anında **"Görev tamamlandı."** mesajı gönderir.

## Tek komutla Oracle kurulumu

Oracle VM'de, `ubuntu` kullanıcısıyla:

```bash
curl -fsSL https://raw.githubusercontent.com/isyerim3406/yapayzeka/main/fiction-comic/install.py -o /tmp/fiction-comic-install.py && python3 /tmp/fiction-comic-install.py
```

Kurulum sonrasında Fiction Studio sayfasını yenileyin. Çalışan eski Kaggle oturumu varsa bir kez kapatıp yeniden açın; kurulum eski worker kodundaki RealVisXL tokenizer değişkenini düzeltir. Yeni eklenti GPU'nun mevcut köprü üzerinden çalışmasına ihtiyaç duyar. Cydonia ve RealVisXL model dosyalarını yeniden kurmaz.

## Kullanım

- Hikâye veya her satıra bir URL yapıştırıp **Comic Book Hazırla**'ya basın.
- Metin kutusu boşsa veritabanındaki **son Cydonia/asistan yanıtı** kullanılır.
- PDF hazır olduğunda **Biten İşler · Comic PDF / PDF Aç** bağlantısından yeni sekmede açılır.

### Telegram

Oracle'da `/home/ubuntu/fiction-gpu/comic_telegram.env` dosyası oluşturup izinlerini `chmod 600` ile sınırlandırın. İçeriği:

```ini
COMIC_TELEGRAM_BOT_TOKEN=... 
COMIC_TELEGRAM_CHAT_ID=...
```

Bu dosya yoksa Comic PDF oluşturulur ancak Telegram bildirimi gönderilemez.

## Önemli sınırlar

- **Herkese açık** statik HTML sayfaları desteklenir; CAPTCHA, ücretli giriş, JavaScript ile yüklenen metinler ve korumalı sayfalar otomatik aşılamaz.
- URL'lere bağlantı Oracle'dan gerçekleştirilir; siteler Oracle'ın IP adresini görebilir.
- Aynı anda tek GPU işi başlatılabilir. Uzun hikâyeler için 60 panel sınırı bulunur; Cydonia çıktı biçimini bozarsa iş hata durumuna geçebilir.
- GPU oturumu veya Oracle web servisi yeniden başlatılırsa uzun iş kesilebilir; eklenti otomatik devam sistemi içermez.
- PDF'ler Oracle'daki `comic_output` altında, erişimi sınırlandırılmış dosya izinleriyle saklanır; **disk üzerinde şifrelenmiş değildir**. Şifreli dosya saklama ayrı geliştirme gerektirir.
- Mevcut kaynak dosyalar için **otomatik yedek oluşturulmaz**; kurulum `app.py`, `templates/index.html` ve worker dosyasına gerektiğinde doğrudan değişiklik yapar.
- Bu kodun URL/parçalama ve PDF üretim birim testleri yapıldı. Canlı Oracle/Kaggle üzerinden uçtan uca üretim henüz doğrulanmadı.
