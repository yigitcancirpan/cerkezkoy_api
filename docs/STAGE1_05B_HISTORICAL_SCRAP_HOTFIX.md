# Aşama 1.05b — Geçmiş fire OEE koruması

## Düzeltilen hata

Geçmiş tarihli fire eklendiğinde vardiya özeti güncel vardiya tanımıyla
baştan hesaplanıyordu. Genel vardiya sonradan 08:00–20:00 yapıldığında,
geçmişte 08:00–18:00 çalışan günlerin kullanılabilirlik, performans ve OEE
değerleri bozuluyordu.

## Yeni kural

Geçmiş fire ekleme veya silme işlemi yalnız şu alanları günceller:

- `total_scrap`
- `total_good`
- `oee_quality`
- `oee_overall`
- `calculated_at`

Üretim, vardiya süresi, mola, duruş, kullanılabilirlik, performans ve ideal
çevrim alanları korunur. Fire ve vardiya özeti aynı transaction içinde
güncellenmeye devam eder.

Geçmiş fire ekranı, tamamlanmış bir vardiya özeti varsa güncel genel vardiya
saatini değil özetin kaydedilmiş `planned_base_sec` süresini gösterir.

## Uygulama

Bu yama, Aşama 1.05 yaması uygulanmış fakat henüz commit edilmemiş çalışma
ağacına uygulanır.

```bash
git apply --check stage1_05b_historical_scrap_quality_only.patch
git apply stage1_05b_historical_scrap_quality_only.patch

sudo -u server /usr/bin/python3 -m py_compile \
  routers/scrap.py \
  services/scrap_service.py

sudo -u server /usr/bin/python3 -m unittest discover -v
git diff --check
```

API yeniden başlatıldıktan sonra 18 Ağustos seçeneklerinde `08:00–18:00`,
24 Ağustos seçeneklerinde `08:00–20:00` görünmelidir.

## Veritabanı

Bu hotfix yeni kolon veya tablo gerektirmez. Daha önce bozulmuş özetler,
`2026-08-25 10:22:22+03` yedeğindeki `shift_summary` bileşenleri kullanılarak
onarılmıştır. Yeni girilen fire kayıtları korunmuştur.
