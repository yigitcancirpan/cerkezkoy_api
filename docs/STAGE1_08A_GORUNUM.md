# Aşama 1.08a — Malzeme görünümü düzeltmesi

Bu düzeltme Aşama 1.08 üzerine uygulanır ve veritabanı migration'ı içermez.

- Malzemeye göre üretim/kalite arama kartı yalnızca **Geçmiş & Analiz** ekranında bulunur.
- Arama kartı sitenin renk, kart, alan ve tablo biçimini kullanır; sayfa başlığının üstüne eklenmez.
- Malzeme ve tarih seçimi; arama tablosunu, üretim/fire KPI'larını ve günlük üretim grafiğini birlikte filtreler. OEE hat toplamı olarak açıkça etiketlenir.
- Dashboard'daki üretim kartında aktif malzeme kodu ve adı görünür. Üretim adedi hat vardiya toplamıdır.
- Dashboard Vardiya Geçmişi satırına basıldığında o gün ve vardiyada üretilen malzemeler ayrı açılır: üretim, fire ve sağlam adet gösterilir.
- Duruş ayrıntısı bağlantısı vardiya satırında korunur.
- Planlama ekranında iki ürün arasında geçiş için başlıkta küçük bir malzeme seçici bulunur.
- Fire ekranı kendi malzeme seçicisini yükler; ortak büyük arama paneline bağımlı değildir.
- Ana sayfa, dashboard, planlama, fire ve duruş ekranlarında büyük malzeme arama kartı gösterilmez.

Eski üretim/fire kayıtları kesin eşleştirilemiyorsa `Belirsiz` kalır. Malzeme seçilmiş olsa da OEE ortak hat OEE'sidir.

Kurulum:

```bash
cd /home/server/cerkezkoy_api
unzip -n stage1_08a_history_ui_package.zip
bash stage1_08a_history_ui/install.sh --apply
```

`INSTALL_RESULT=OK` sonrasında Git ve paket temizliği:

```bash
bash stage1_08a_history_ui/finish_git.sh
```

Kurulumdan sonra tarayıcıda `Ctrl+F5` ile önbelleği yenileyin.
