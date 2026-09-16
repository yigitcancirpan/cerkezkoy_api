"""Hat bağlamı gerektiren statik sayfalar için URL doğrulaması."""

from collections.abc import Mapping
import re
from urllib.parse import urlencode


LINE_SCOPED_STATIC_PAGES = frozenset(
    {
        "/static/admin.html",
        "/static/analytics.html",
        "/static/dashboard.html",
        "/static/downtime_monitor.html",
        "/static/downtimes.html",
        "/static/planning.html",
        "/static/scrap.html",
        "/static/setup.html",
    }
)

_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]*$")


def get_line_id(query: Mapping[str, str]) -> int | None:
    """`line`/`line_id` değerini doğrula; çelişki veya hata varsa None dön."""
    raw_values = [
        value
        for key in ("line", "line_id")
        if (value := query.get(key)) is not None
    ]
    if not raw_values or any(not _POSITIVE_INTEGER.fullmatch(v) for v in raw_values):
        return None

    line_ids = {int(value) for value in raw_values}
    if len(line_ids) != 1:
        return None
    return line_ids.pop()


def line_context_redirect_url(path: str, query: Mapping[str, str]) -> str | None:
    """Hat bağlamı eksik/geçersizse ana sayfa yönlendirme URL'sini üret."""
    if path not in LINE_SCOPED_STATIC_PAGES or get_line_id(query) is not None:
        return None

    requested_page = path.rsplit("/", 1)[-1].removesuffix(".html")
    return "/static/home.html?" + urlencode(
        {"notice": "line_required", "requested": requested_page}
    )
