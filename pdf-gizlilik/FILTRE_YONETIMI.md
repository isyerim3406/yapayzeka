# Uygulama içi filtre yönetimi

Ana ekrandaki **Filtre Yönetimi** düğmesiyle açılır. 111 genişletilmiş filtreye mevcut motorun 17 deseni eklenmiştir. Toplam 128 başlangıç kuralı gömülüdür. Ayrı HTML veya JSON kullanmak gerekmez.

1. Yeni filtreye basın. Başlık veya birebir ifade türünü seçin.
2. Ad ve eşanlamlı başlıkları (her satıra bir tane) girin. Çok satırlı değer seçeneğini gerektiğinde açın.
3. Kaydet düğmesine basın, pencereyi kapatıp açık PDF’yi yeniden tarayın.

Kurallar kullanıcı profilinde yerel olarak saklanır. EXE yeni sürümle değiştirildiğinde aynı Windows kullanıcısının filtreleri korunur. Ağ bağlantısı, otomatik yedek ve belge metni günlükleri yoktur. Kaydetme başarısız olursa eski dosya korunur ve hata gösterilir.

Kapalı başlıklar sınır olarak kullanılmaya devam eder; komşu alanın onları yutması önlenir. Filtre değiştikten sonra yeniden taramadan çıktı alınamaz. Başka filtrelerin aynı değeri yakalaması mümkündür; bulunan bilgi listesi son kontroldür.

Metin katmanı ve OCR sözcüklerinin koordinatları birlikte değerlendirilir. Satır içindeki bir sonraki başlıkta durulur; devam satırları yakınlık ve hizalamayla sınırlanır. Bu yöntem tüm karmaşık tabloları veya OCR hatalarını çözmez. İmza, kaşe, diyagonal filigran ve konumu açığa çıkaran görselleri ayrıca kontrol edin.

Geliştirme: default_filters.json PyInstaller paketine gömülür; filters.py kalıcı veri ve aday tespiti, filter_dialog.py yerel Tk arayüzüdür. Çevrimiçi firmalar/vergi mükellefleri veritabanı tutulmaz.
