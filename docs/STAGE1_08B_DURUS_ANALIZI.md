# Aşama 1.08b — Geçmiş duruş KPI analizi

Geçmiş & Analiz ekranına, seçilen tarih aralığı ve vardiya için duruş KPI'larını gösteren ayrı bir sekme ekler.

Gösterilen metrikler:

- brüt vardiya süresi ve üretime ayrılan süre;
- OEE vardiya özetlerinden net çalışma süresi ve kullanılabilirlik;
- kayıtlı duruşların toplamı ile planlı/plansız ayrımı;
- duruş adedi, ortalama duruş ve en yüksek süre kaybettiren sebep;
- her duruş sebebi için toplam süre, adet, ortalama ve toplam içindeki pay;
- seçilen sebebin günlere göre süre dağılımı.

`VARDIYA_SONU` ve `BELIRLENMEDI` teknik kayıtları kullanıcı analizine katılmaz. Net çalışma verisi vardiya özetlerinden, sebep süreleri tamamlanmış duruş kayıtlarından gelir. Bu sürüm veritabanı migration'ı içermez.
