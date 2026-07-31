#!/usr/bin/env python3
"""
Günlük üretim raporu — her akşam 20:00.
Son 7 günün vardiya raporu Excel'ini mevcut endpoint'ten alır, e-postaya ekler.
Gövdede kısa özet, detay ekteki .xlsx'te. Sadece stdlib + smtplib.
"""
import os, json, smtplib, ssl, sys
from datetime import date, timedelta
from urllib.request import urlopen
from urllib.error import URLError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

API       = os.environ.get("REPORT_API_BASE", "http://localhost:8000")
LINE_ID   = int(os.environ.get("REPORT_LINE_ID", "1"))
LINE_NAME = os.environ.get("REPORT_LINE_NAME", "Qs Hattı")
DAYS      = int(os.environ.get("REPORT_DAYS", "7"))

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "toramakinaerp@toramakina.com")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)
MAIL_TO   = [x.strip() for x in os.environ.get("MAIL_TO", "").split(",") if x.strip()]

HIDDEN_DT = {"VARDIYA_SONU", "BELIRLENMEDI"}
LINE_ID   = int(os.environ.get("REPORT_LINE_ID", "1"))
LINE_NAME = os.environ.get("REPORT_LINE_NAME", "Qs Hattı")

# LINE_ID / LINE_NAME sabitleri kaldırıldı — hatlar API'den geliyor
def fetch_lines():
    lines = fetch_json("/api/v1/lines") or []
    active = [l for l in lines if l.get("is_active", True)]
    return active or [{"line_id": 1, "line_name": "Qs Hattı"}]   # emniyet

def fetch_json(path):
    try:
        with urlopen(f"{API}{path}", timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except (URLError, Exception) as e:
        print(f"[uyarı] {path} alınamadı: {e}", file=sys.stderr)
        return None


def fetch_excel_all(start, end):
    """Tüm hatların raporunu tek ZIP olarak çek (/vardiya-excel-all)."""
    url = f"{API}/api/v1/report/vardiya-excel-all?start={start}&end={end}"
    try:
        with urlopen(url, timeout=90) as r:
            data = r.read()
        if not data or len(data) < 200:
            print(f"[uyarı] ZIP boş/şüpheli döndü ({len(data)} byte)", file=sys.stderr)
            return None
        return data
    except (URLError, Exception) as e:
        print(f"[hata] ZIP alınamadı: {e}", file=sys.stderr)
        return None


def fmt_dur(sec):
    sec = int(sec or 0)
    if sec < 60: return f"{sec} sn"
    m = sec // 60
    return f"{m} dk" if m < 60 else f"{m // 60} sa {m % 60} dk"


def collect_summary(line_id, start, end):
    shifts = fetch_json(f"/api/v1/production/shifts?line_id={line_id}&days={DAYS + 1}") or []
    in_range = [s for s in shifts if start <= s.get("shift_date", "") <= end]
    produced = sum(s.get("total_produced", 0) for s in in_range)
    scrap    = sum(s.get("total_scrap", 0) for s in in_range)

    dt = fetch_json(f"/api/v1/downtimes/analysis?line_id={line_id}&date_from={start}&date_to={end}") or {}
    dt_reasons = [r for r in (dt.get("reasons") or []) if r.get("reason_code") not in HIDDEN_DT]
    dt_total = sum(r["total_sec"] for r in dt_reasons)
    dt_reasons.sort(key=lambda x: x["total_sec"], reverse=True)

    return {"produced": produced, "scrap": scrap, "shift_count": len(in_range),
            "dt_total": dt_total, "top_dt": dt_reasons[:3]}


def _line_section(line_name, s):
    rate = (s["scrap"] / s["produced"] * 100) if s["produced"] else 0
    top = "".join(
        f"<li style='margin:2px 0'>{r['reason_name']} — {fmt_dur(r['total_sec'])} ({r['count']} kez)</li>"
        for r in s["top_dt"]
    ) or "<li style='color:#999'>Kayıt yok</li>"
    return f"""\
  <h2 style="margin:18px 0 4px">{line_name}</h2>
  <p style="margin:0 0 12px;color:#666">{s['shift_count']} vardiya</p>
  <table style="width:100%;border-collapse:collapse;margin-bottom:14px">
    <tr>
      <td style="padding:12px;background:#eafaf1;border-radius:6px;text-align:center">
        <div style="font-size:22px;font-weight:700;color:#27ae60">{s['produced']}</div>
        <div style="font-size:11px;color:#666">Toplam Üretim</div></td>
      <td style="width:8px"></td>
      <td style="padding:12px;background:#fdedec;border-radius:6px;text-align:center">
        <div style="font-size:22px;font-weight:700;color:#c0392b">{s['scrap']}</div>
        <div style="font-size:11px;color:#666">Toplam Fire (%{rate:.2f})</div></td>
      <td style="width:8px"></td>
      <td style="padding:12px;background:#fef5e7;border-radius:6px;text-align:center">
        <div style="font-size:22px;font-weight:700;color:#e67e22">{fmt_dur(s['dt_total'])}</div>
        <div style="font-size:11px;color:#666">Toplam Duruş</div></td>
    </tr>
  </table>
  <h3 style="margin:0 0 6px;font-size:14px">En Çok Süre Yiyen Duruşlar</h3>
  <ul style="font-size:13px;padding-left:18px;margin:0 0 8px">{top}</ul>"""


def build_html(sections, start, end):
    body = "".join(sections)
    return f"""\
<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#1a1a1a">
  <h1 style="margin:0 0 4px;font-size:20px">Haftalık Üretim Raporu — Tüm Hatlar</h1>
  <p style="margin:0 0 8px;color:#666">{start} → {end}</p>
  {body}
  <p style="font-size:13px;color:#444;background:#f5f5f5;padding:10px;border-radius:6px;margin-top:16px">
    📎 Her hattın günlük detayları (saat saat duruşlar, vardiya kırılımı) ekteki ZIP içindeki ayrı Excel dosyalarındadır.</p>
  <p style="font-size:11px;color:#999;border-top:1px solid #eee;padding-top:10px">
    Fabrika IoT sistemi tarafından otomatik üretilmiştir.</p>
</div>"""


def main():
    if not SMTP_PASS:
        print("[hata] SMTP_PASS tanımlı değil — iptal.", file=sys.stderr)
        sys.exit(1)

    end   = date.today().isoformat()
    start = (date.today() - timedelta(days=DAYS - 1)).isoformat()

    lines = fetch_lines()
    sections = []
    for ln in lines:
        s = collect_summary(ln["line_id"], start, end)
        sections.append(_line_section(ln["line_name"], s))

    zip_bytes = fetch_excel_all(start, end)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Haftalık Üretim Raporu — Tüm Hatlar ({start}→{end})"
    msg["From"] = MAIL_FROM
    msg["To"]   = ", ".join(MAIL_TO)
    msg.attach(MIMEText(build_html(sections, start, end), "html", "utf-8"))

    if zip_bytes:
        att = MIMEApplication(zip_bytes, _subtype="zip")
        att.add_header("Content-Disposition", "attachment",
                       filename=f"vardiya_raporu_tum_hatlar_{start}_{end}.zip")
        msg.attach(att)
    else:
        print("[uyarı] ZIP eklenemedi — sadece özet gönderiliyor.", file=sys.stderr)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg, from_addr=MAIL_FROM, to_addrs=MAIL_TO)
    print(f"[ok] Rapor gönderildi → {', '.join(MAIL_TO)}"
          + (" (Excel ekli)" if xlsx_bytes else " (Excel YOK)"))


if __name__ == "__main__":
    main()