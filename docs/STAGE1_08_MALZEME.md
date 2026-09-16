# Aşama 1.08 — Malzeme bazında üretim ve planlama

Bu sürüm, yüklenen canlı 1.07c r1 kaynaklarının üzerine hazırlanmıştır.

## Kullanım

- Ana sayfa, dashboard, geçmiş, fire, planlama ve duruş ekranlarının üstünde malzeme bazında üretim/kalite raporu bulunur. Tarih, hat ve malzeme seçilebilir; CSV indirilebilir.
- Geçmiş ekranındaki üretim grafiği ve adetler malzeme filtresini kullanır. OEE, duruş, enerji ve hat hedefleri hat düzeyindedir; bu veriler malzemeye aitmiş gibi gösterilmez.
- Fire girişinde malzeme seçilebilir. Karışık veya belirsiz üretim varsa seçim zorunludur. Fire, seçilen malzemenin üretimini aşamaz.
- Planlama ekranında 2962070700 ve 2962070100 ayrı sipariş, mamul, hammadde, günlük plan ve kapasite geçmişine sahiptir.
- Otomatik plandaki “Minimum gün sonu stok” ilk açılışta seçilen malzemenin stok ayarından gelir. Bu alandaki değişiklik yalnızca o planı etkiler. Her gün minimum ve stok farkı gösterilir; sağlanamayan hedef gizlenmez.
- Yeni malzemenin çalışma takvimi başlangıçta boştur. Çalışma günlerini ve stok tarihini seçin; geçmiş kapasite yoksa doğrulanmış kapasite girin.
- Geçmiş → Dashboard bağlantısı hat ve malzeme seçimini korur.

## Malzeme kimliği ve geçmiş

2962070100 kaydında model_id boştu. Migration, malzemelere ve üretim kayıtlarına bakarak kullanılmamış pozitif model numarası oluşturur. Mevcut pozitif model kimliği değiştirilemez. Yeni malzemelerde boş model alanı otomatik doldurulur.

Kurulumdan sonra **Atama ekranında 2962070100 malzemesini ilgili makineye tekrar atayın**. Böylece yeni model numarası mevcut atama/MQTT akışıyla gönderilir. Kurulum kendiliğinden makineye üretim komutu göndermez. İlk üretimden sonra dashboard malzeme kodunu kontrol edin.

Model 0/boş kayıtlar sonradan yeni malzemeye çevrilmez. Eksik/çelişkili model detayları “Belirsiz” kalır. Tarihsel fire yalnızca üretimin tamamı tek bir malzemeyle kesin eşleşiyorsa geri doldurulur. Malzemesi bilinmeyen fire varsa sağlam adet “—” gösterilir. Sayaç örnekleme aralığında malzeme değişimi kesin parça düzeyinde ayrıştırılamayabilir.

## Paylaşılan hat ve hammadde

Bu sürüm iki malzeme için ayrı plan üretir; aynı gün içinde kalıp değiştirerek ortak optimizasyon yapmaz. Aynı hat ve günde başka malzemenin pozitif günlük planı varsa onay/günlük kayıt 409 ile durur. Günleri malzemelere ayırın; mevcut planı sıfırlamadan aynı güne ikinci tam günlük plan eklemeyin. Haftalık varsayılanlar tahmindir, hat rezervasyonu değildir.

Hammadde stokları ürün bazındadır. İki ürün aynı fiziksel hammadde stoğunu tüketiyorsa ortak stoğun tamamını iki ürüne birden girmeyin; ayrılan miktarları girin. Ortak hammadde havuzu ve aynı gün çok malzemeli çizelgeleme bu sürümün kapsamı dışındadır.

## Teknik değişiklikler

- material_model_seq, pozitif model kimliği unique index/koruma trigger'ı.
- scrap_entries.material_id nullable foreign key ve tarih/malzeme index'i.
- /api/v1/material-report ve /csv; en fazla 93 günlük aralık.
- Vardiya model detaylarından malzeme miktarı; eksik özetlerde resetleri dikkate alan sayaç farkları. Özet ve günlük kayıt birlikte toplanmaz.
- Anlık hat adedi de sayaç resetini ve vardiyanın gece yarısını aşmasını dikkate alır.
- Eski otomatik planlar yeni hesaplama kuralıyla yeniden oluşturulmalıdır; geçmiş onay kayıtları silinmez.
- Mola kapasitesi, dönüşümlü mola ve 18:00–18:30 yemek molası mevcut 1.07c davranışını korur.

## Kurulum ve geri dönüş

Paket `install.sh --apply` ile kaynak uyumunu ve testleri kontrol eder; kod/veritabanı yedeği alır, SQL'i stdin üzerinden uygular, kodu yerleştirir ve yalnızca cerkezkoy_api servisini yeniden başlatır. Node yoksa daha önce doğrulanmış JavaScript nedeniyle kurulum engellenmez. Beklenmeyen kaynak sürümü üzerine yazılmaz.

Yedek yolu çıktıdaki BACKUP satırıdır. Kod geri dönüşü gerekiyorsa servis durdurularak o dizindeki code_before.tar.gz proje köküne açılır. Daha önce mevcut olmayan yeni dosyalar backup manifestinde listelidir. SQL eklemeleri geriye uyumludur; üretim başladıktan sonra yeni model kimliklerini veya material_id alanını kaldırmayın. Tam veritabanı geri yüklemesi yeni üretim verisini kaybettirebilir; otomatik yapılmaz.

`finish_git.sh` kurulum/sağlık kontrolünden sonra bilinen kaynakları ve migration belgelerini commit/push eder. Başka staged değişiklik varsa veya origin/main yerelden ilerideyse durur; force push/otomatik merge yapmaz. Push başarılı olduktan sonra yalnızca bilinen kurulum ZIP'leri doğrulanmış kopyalarıyla /var/backups/cerkezkoy_api altında arşivlenir ve proje kökünden kaldırılır. .env, veritabanı ve çalışma yedekleri Git'e eklenmez.

`migrations/history` önceki planlama migration'larını sürüm kontrolünde tutar. Mevcut canlı veritabanında bunları yeniden çalıştırmayın; 1.08 kurulumu yalnızca yeni migration'ı uygular.
