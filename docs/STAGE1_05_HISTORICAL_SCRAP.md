# Aşama 1.05 — Geçmiş fire girişi ve OEE yeniden hesaplama

## Amaç

- Operatör bugün ve en fazla 7 gün geriye fire girebilir.
- Üretim tarihi ile gerçek kayıt zamanı birbirinden ayrılır.
- Tamamlanmış vardiya özeti varsa fire ekleme/silme ile OEE v3 aynı
  transaction içinde yeniden hesaplanır.
- Hesaplama başarısız olursa fire değişikliği de geri alınır.

## Veri modeli

`scrap_entries.production_date`, firenin ait olduğu üretim günüdür.
`scrap_entries.created_at`, kaydın sisteme gerçekten eklendiği zamandır.
Eski kayıtların `production_date` değeri İstanbul saat dilimindeki
`created_at` tarihinden kayıpsız doldurulur. Boş eski vardiyalar, mevcut tek
vardiya kodu `vardiya_1` ile tamamlanır.

## API

- `GET /api/v1/scrap/entry-options?line_id=2&target_date=YYYY-MM-DD`
- `POST /api/v1/scrap/entry`
- `DELETE /api/v1/scrap/entry/{id}`

Geçmiş kayıt örneği:

```json
{
  "line_id": 2,
  "reason_id": 1,
  "qty": 3,
  "production_date": "2026-08-22",
  "shift": "vardiya_1",
  "notes": "Vardiya sonrasında bildirildi",
  "source": "historical"
}
```

Yanıttaki `summary_recalculated=true`, mevcut vardiya özetinin OEE v3 ile
yenilendiğini gösterir. Özet yoksa fire kaydı oluşturulur fakat yapay bir
üretim özeti üretilmez.

## Devreye alma sırası

1. Güncel PostgreSQL yedeği alınır ve `pg_restore --list` ile doğrulanır.
2. `stage1_05_historical_scrap.sql` transaction içinde uygulanır.
3. Kod patch'i uygulanır; Python, JavaScript ve unit testleri çalıştırılır.
4. API ve production logger yeniden başlatılır.
5. `/entry-options`, `/health` ve servis logları doğrulanır.
6. Gerçek bir geçmiş fire girişi arayüzden yapılır; ilgili `shift_summary`
   satırındaki fire, iyi üretim, kalite ve genel OEE karşılaştırılır.

## Üretim sunucusu komutları

### Ön kontrol

```bash
cd /home/server/cerkezkoy_api

git status --short

sudo -u postgres psql -d cerkezkoy_db -X -P pager=off -c "
SELECT
    COUNT(*) AS total_entries,
    COUNT(*) FILTER (WHERE shift IS NULL OR BTRIM(shift)='') AS missing_shift,
    MIN(created_at) AS first_entry,
    MAX(created_at) AS last_entry
FROM public.scrap_entries;
"
```

### Güncel yedek

```bash
sudo -u postgres pg_dump \
  --format=custom \
  --compress=6 \
  --file=/var/backups/cerkezkoy_api/cerkezkoy_db_2026-08-24_before_historical_scrap.dump \
  cerkezkoy_db

sudo -u postgres pg_restore --list \
  /var/backups/cerkezkoy_api/cerkezkoy_db_2026-08-24_before_historical_scrap.dump \
  | head -20

sudo chmod 600 \
  /var/backups/cerkezkoy_api/cerkezkoy_db_2026-08-24_before_historical_scrap.dump
```

### Migration

```bash
set -o pipefail

sudo -u postgres psql \
  -d cerkezkoy_db \
  -X \
  -v ON_ERROR_STOP=1 \
  -f stage1_05_historical_scrap.sql \
  2>&1 | tee stage1_05_historical_scrap_migration.log

MIGRATION_RESULT="${PIPESTATUS[0]}"
echo "MIGRATION_RESULT=$MIGRATION_RESULT"
```

`MIGRATION_RESULT=0`, `missing_production_date=0` ve `missing_shift=0`
görülmeden kod devreye alınmaz.

### Kod

```bash
git apply --check stage1_05_historical_scrap.patch
git apply stage1_05_historical_scrap.patch

sudo -u server /usr/bin/python3 -m py_compile \
  routers/scrap.py \
  services/scrap_service.py \
  services/oee_service.py

sudo -u server /usr/bin/python3 -m unittest discover -v
git diff --check
```

### Kısa restart ve kontrol

```bash
sudo systemctl restart cerkezkoy_api production_logger

for second in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8000/health; then
        echo
        echo "API_RESTART=SUCCESS second=$second"
        break
    fi
    sleep 1
done

curl -fsS \
  "http://127.0.0.1:8000/api/v1/scrap/entry-options?line_id=2&target_date=2026-08-22" \
  | /usr/bin/python3 -m json.tool
```

Arayüz:

```text
http://192.168.34.52:8000/static/scrap.html?line=2
```

## Geri alma

Kod, patch ters uygulanarak geri alınabilir. Migration sonrası eski
`created_at`, `shift` ve tüm fire kayıtları aynen kalır. `production_date`
kolonunun hemen silinmesi gerekmez; eski kod bu kolonu görmezden gelir.
Bu nedenle acil geri dönüş veri kaybı olmadan yalnızca kod ve servis restartı
ile yapılabilir.

```bash
git apply -R stage1_05_historical_scrap.patch
sudo systemctl restart cerkezkoy_api production_logger
curl -fsS http://127.0.0.1:8000/health
echo
```
