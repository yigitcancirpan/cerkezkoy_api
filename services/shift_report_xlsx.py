"""Vardiya raporu Excel üretici — SAF STANDART KÜTÜPHANE (openpyxl YOK).
.xlsx = zip + XML olduğu için harici bağımlılık gerektirmez. Air-gapped uyumlu.
build_report(rows) -> bytes."""
import zipfile
from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape

BASE_HEADERS = ["Tarih", "Başlangıç Saati", "Bitiş Saati", "T. Üretim Süresi (saat)",
                "Mola (saat)", "Hedef", "Toplam Üretim Adedi", "Hurda Adedi", "Sağlam Üretim Adedi"]

SHIFT_CFG = {
    "vardiya_1": {"start": "08:00", "end": "18:00", "prod_h": 10.0, "break_h": 1.0},
    "vardiya_2": {"start": "18:00", "end": "00:00", "prod_h": 6.0,  "break_h": 0.5},
}
DEFAULT_CFG = {"start": "08:00", "end": "18:00", "prod_h": 10.0, "break_h": 1.0}


def _colref(idx):  # 1 -> A, 27 -> AA
    s = ""
    while idx > 0:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


# ---- statik paket parçaları ----
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WB = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Vardiya Raporu" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="10"/><name val="Arial"/></font>
<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>
<font><b/><sz val="14"/><color rgb="FF1F3864"/><name val="Arial"/></font>
<font><b/><sz val="10"/><name val="Arial"/></font>
</fonts>
<fills count="6">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFFFFF"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right><top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="8">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _cell(ref, style, value):
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, bool):
        value = str(value)
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def build_report(rows: list) -> bytes:
    max_dt = max((len(r["downtimes"]) for r in rows), default=1) or 1
    ncols = len(BASE_HEADERS) + max_dt * 2 + 1
    last_col = _colref(ncols)

    # cols (genişlikler)
    widths = [12, 14, 11, 16, 10, 9, 16, 11, 16]
    for _ in range(max_dt):
        widths += [13, 24]
    widths += [13]
    cols_xml = "<cols>" + "".join(
        f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)
    ) + "</cols>"

    sheet_rows = []

    # satır 1: başlık
    sheet_rows.append(f'<row r="1" ht="22" customHeight="1">{_cell("A1", 1, "Qs HATTI — GÜNLÜK VARDİYA RAPORU")}</row>')

    # satır 3: header
    HR = 3
    cells = []
    col = 1
    for h in BASE_HEADERS:
        cells.append(_cell(f"{_colref(col)}{HR}", 2, h)); col += 1
    for i in range(1, max_dt + 1):
        cells.append(_cell(f"{_colref(col)}{HR}", 2, f"Duruş {i} Zamanı")); col += 1
        cells.append(_cell(f"{_colref(col)}{HR}", 2, f"Duruş {i} Nedeni")); col += 1
    cells.append(_cell(f"{_colref(col)}{HR}", 2, "Kayıp Toplam (DK)"))
    sheet_rows.append(f'<row r="{HR}" ht="30" customHeight="1">{"".join(cells)}</row>')

    # veri satırları
    rnum = HR + 1
    for ri, row in enumerate(rows):
        cfg = SHIFT_CFG.get(row.get("shift"), DEFAULT_CFG)
        fmt = lambda v: "-" if v is None else v
        head = [row["date"], cfg["start"], cfg["end"], cfg["prod_h"], cfg["break_h"],
                fmt(row.get("target")), fmt(row.get("total_produced")),
                fmt(row.get("total_scrap")), fmt(row.get("total_good"))]
        shaded = ri % 2 == 1
        s_center = 5 if shaded else 3
        s_left = 6 if shaded else 4
        cells = []
        col = 1
        for j, v in enumerate(head):
            st = s_left if j == 0 else s_center
            cells.append(_cell(f"{_colref(col)}{rnum}", st, v)); col += 1
        for i in range(max_dt):
            z, n = row["downtimes"][i] if i < len(row["downtimes"]) else ("", "")
            cells.append(_cell(f"{_colref(col)}{rnum}", s_center, z)); col += 1
            cells.append(_cell(f"{_colref(col)}{rnum}", s_left, n)); col += 1
        cells.append(_cell(f"{_colref(col)}{rnum}", 7, row.get("lost_min")))
        sheet_rows.append(f'<row r="{rnum}">{"".join(cells)}</row>')
        rnum += 1

    last_row = rnum - 1
    merge = f'<mergeCells count="1"><mergeCell ref="A1:{last_col}1"/></mergeCells>'
    pane = '<sheetViews><sheetView workbookViewId="0"><pane xSplit="1" ySplit="3" topLeftCell="B4" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>'

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_col}{last_row}"/>'
        f'{pane}'
        f'{cols_xml}'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        f'{merge}'
        '</worksheet>'
    )

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", _WB)
        z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


if __name__ == "__main__":
    test = [
        {"date": "1.06.2026", "shift": "vardiya_1", "target": 3000,
         "total_produced": None, "total_scrap": None, "total_good": 2236,
         "downtimes": [("08:00-08:40", "Makine Arızası"), ("11:15-11:30", "Makine Arızası"),
                       ("17:10-18:00", "Makine Arızası")], "lost_min": 240},
        {"date": "16.06.2026", "shift": "vardiya_1", "target": 3000,
         "total_produced": 2668, "total_scrap": 15, "total_good": 2653,
         "downtimes": [("08:00-08:10", "Makine Ayarı"), ("16:35-17:10", "Makine Arızası & test")],
         "lost_min": 140},
    ]
    open("/home/claude/work/test2.xlsx", "wb").write(build_report(test))
    print("yazildi")