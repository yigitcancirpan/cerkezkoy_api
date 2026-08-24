# Aşama 1.03 — OEE v3, ürün çevrimi ve gerçek mola süresi

Bu aşama canlı OEE, vardiya özeti ve geçmiş yeniden hesaplama için tek formül
kullanır. Duruşlar planlı mola pencerelerinde otomatik bölünür; duruş analizi
eski birleşik kayıtları da ekranda aynı kuralla sanal olarak böler.

## Kesin iş kuralları

- Vardiya süresi brüttür. Örneğin 08:00–18:00 = 36.000 saniye.
- Mola çizelgesi tek başına OEE süresi düşürmez.
- Yalnızca `gerçek duruş ∩ tanımlı mola penceresi` planlı mola sayılır.
- Üretim nöbetleşe operatörle mola boyunca sürerse düşülen mola süresi `0` olur.
- Mola dışına taşan süre plansızdır. 09:55–10:20 duruşu, 10:00–10:15
  mola penceresinde 5 dk plansız + 15 dk planlı mola + 5 dk plansız olur.
- `OPERATOR` sebebinin çizelge dışındaki kısmı plansız sayılır. Bu kural OEE'nin
  yanlışlıkla yükseltilmesini engeller.
- İdeal çevrim ölçülen ortalama çevrim değildir. Üretilen ürünün/malzemenin
  `ideal_cycle_ds` değeridir; bulunamazsa hat varsayılanı kullanılır.
- Bir vardiyada farklı ürünler varsa her ürün adedi kendi ideal çevrimiyle
  çarpılır. Aynı `model_id` için çelişkili çevrim varsa hesap yazılmaz.
- Kümülatif sayaçta vardiya üretimi pozitif sayaç farklarının toplamıdır.
  Sayaç düşüşü reset kabul edilir; audit çıktısında ayrıca raporlanır.

## Formül

- Planlanan üretim = brüt vardiya − gerçek planlı mola duruşu
- Çalışma = planlanan üretim − plansız duruş
- Kullanılabilirlik = çalışma / planlanan üretim
- İdeal süre = Σ(ürün adedi × ürün ideal çevrimi)
- Performans = ideal süre / çalışma
- Kalite = iyi üretim / toplam üretim
- OEE = kullanılabilirlik × performans × kalite

Performans `%100` ile sınırlandırılır. Çakışan duruşlar iki kez sayılmaz.

## 1. Salt okunur ön denetim

```bash
cd /home/server/cerkezkoy_api

sudo -u postgres psql \
  -d cerkezkoy_db \
  -X \
  -f stage1_03_oee_audit.sql \
  | tee stage1_03_oee_audit.log
```

Şunları kontrol edin:

1. Üretim geçmişindeki her `model_id` için malzemede `ideal_cycle_ds` vardır
   veya ilgili hatta güvenilir `production_lines.ideal_cycle_ds` tanımlıdır.
2. Aynı `model_id` için birden fazla farklı ideal çevrim raporlanmamalıdır.
3. Sayaç düşüşleri gerçek resetlerle uyuşmalıdır.
4. Aktif duruş olmamalıdır.

`ideal_cycle_ds=64`, 6,4 saniye/adet; `90`, 9,0 saniye/adet demektir.

## 2. Yedek

```bash
sudo -u postgres pg_dump \
  --format=custom \
  --compress=6 \
  --file=/var/backups/cerkezkoy_api/cerkezkoy_db_before_oee_v3.dump \
  cerkezkoy_db

sudo chmod 600 \
  /var/backups/cerkezkoy_api/cerkezkoy_db_before_oee_v3.dump

sudo -u postgres pg_restore \
  --list \
  /var/backups/cerkezkoy_api/cerkezkoy_db_before_oee_v3.dump \
  >/dev/null
```

## 3. Kod ve şema

```bash
git apply --check stage1_03_oee_v3.patch
git apply stage1_03_oee_v3.patch

sudo -u server /usr/bin/python3 -m py_compile \
  shift_utils.py \
  services/break_service.py \
  services/oee_service.py \
  services/production_logger.py \
  routers/downtimes.py \
  routers/production.py \
  routers/settings.py \
  recalculate_oee.py

sudo -u server /usr/bin/python3 -m unittest discover -v
git diff --check
```

Migration kayıt silmez. `shift_summary` denetim alanlarını ve
`break_schedules` tablosunu ekler; standart mola pencerelerini yükler.

```bash
sudo -u postgres psql \
  -d cerkezkoy_db \
  -X \
  -v ON_ERROR_STOP=1 \
  -f stage1_03_oee_v2.sql
```

Dosya adı dağıtım uyumluluğu için `v2.sql` olarak korunmuştur; içindeki formül
sürümü `oee-v3`'tür.

## 4. Kısa servis geçişi

Önce API, ardından iki arka plan servisi yeniden başlatılır.

```bash
sudo systemctl restart cerkezkoy_api

for second in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8000/health; then
        echo
        echo "API_RESTART=SUCCESS second=$second"
        break
    fi
    sleep 1
done

sudo systemctl restart downtime_monitor production_logger
sleep 3

sudo systemctl is-active \
  cerkezkoy_api downtime_monitor production_logger mosquitto postgresql
```

## 5. Mola ve API doğrulaması

```bash
curl -fsS \
  http://127.0.0.1:8000/api/v1/settings/breaks \
  | /usr/bin/python3 -m json.tool

curl -fsS \
  'http://127.0.0.1:8000/api/v1/settings/breaks/resolved?target_date=2026-08-22&shift_code=vardiya_1&line_id=2' \
  | /usr/bin/python3 -m json.tool

DATE_TO="$(date -I)"
DATE_FROM="$(date -I -d '15 days ago')"
curl -fsS \
  "http://127.0.0.1:8000/api/v1/production/oee?line_id=2&date_from=$DATE_FROM&date_to=$DATE_TO" \
  | /usr/bin/python3 -m json.tool
```

Yönetim panelindeki **Vardiyalar → Planlı Mola Pencereleri** bölümünde genel,
güne özel veya hatta özel mola tanımlanabilir. Duruş analizi ekranında
kullanılabilirlik, performans, kalite, OEE, ağırlıklı ideal çevrim ve teorik
üretim görünür.

## 6. Geçmiş hesap — önce önizleme

`--apply` olmadan hiçbir özet yazılmaz; işlem sonunda transaction geri alınır.

```bash
sudo systemd-run --wait --pipe --collect \
  --unit="cerkezkoy-oee-dry-run-$(date +%s)" \
  --uid=server --gid=server \
  --property=WorkingDirectory=/home/server/cerkezkoy_api \
  --property=EnvironmentFile=/etc/cerkezkoy-api/cerkezkoy-api.env \
  /usr/bin/python3 /home/server/cerkezkoy_api/recalculate_oee.py \
  --date-from 2026-08-01 \
  --date-to 2026-08-20 \
  --line-id 2 \
  | tee stage1_03_oee_dry_run.log
```

Özellikle `produced`, `downtime_min`, `break_min`, ideal çevrim ve eski→yeni
OEE farklarını inceleyin. Bir ürünün ideal çevrimi eksik/çelişkiliyse işlem
hata vererek durur; veri düzeltilmeden `--apply` kullanılmamalıdır.

```bash
sudo systemd-run --wait --pipe --collect \
  --unit="cerkezkoy-oee-apply-$(date +%s)" \
  --uid=server --gid=server \
  --property=WorkingDirectory=/home/server/cerkezkoy_api \
  --property=EnvironmentFile=/etc/cerkezkoy-api/cerkezkoy-api.env \
  /usr/bin/python3 /home/server/cerkezkoy_api/recalculate_oee.py \
  --date-from 2026-08-01 \
  --date-to 2026-08-20 \
  --line-id 2 \
  --apply \
  | tee stage1_03_oee_apply.log
```

Tek bir hesap hata verirse tüm yazma işlemi geri alınır. `SKIP no_production`
üretimsiz vardiyayı, `SKIP active_downtime` açık duruşu bildirir.

## 7. Son kontrol

```bash
sudo -u postgres psql -d cerkezkoy_db -X -P pager=off -c "
SELECT line_id, shift_date, shift, total_produced, total_scrap,
       total_downtime_sec, break_sec,
       oee_availability, oee_performance, oee_quality, oee_overall,
       ideal_cycle_sec, ideal_cycle_detail,
       formula_version, calculated_at
FROM public.shift_summary
WHERE line_id=2
ORDER BY shift_date DESC, shift
LIMIT 20;
"

sudo journalctl \
  -u cerkezkoy_api -u downtime_monitor -u production_logger \
  --since "10 minutes ago" --no-pager --full \
  | grep -E 'Traceback|ERROR|permission denied|authentication failed' \
  || true
```

## Geri alma

Kod doğrulaması başarısızsa:

```bash
git apply -R stage1_03_oee_v3.patch
sudo systemctl restart cerkezkoy_api downtime_monitor production_logger
curl -fsS http://127.0.0.1:8000/health
echo
```

Nullable özet alanlarını ve `break_schedules` tablosunu bırakmak eski kodu
bozmaz. Geçmiş özetlere `--apply` uygulanmışsa eski sonuçları geri getirmenin
en güvenli yolu işlem öncesi veritabanı yedeğidir; tam geri yükleme üretimi
durduracağı için çalışma saatinde yapılmamalıdır.
