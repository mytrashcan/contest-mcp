"""위비티(wevity.com) — 서버 렌더 목록.

목록 행은 ``<li>`` 안에 제목/분야/주최/현황/조회수가 모두 들어 있다.
마감일은 D-day 배지로만 제공되므로 배지를 날짜로 환산한 뒤 **하루 당긴다**(util.wevity_deadline).
조회수는 경쟁 규모의 프록시로 저장한다.
"""

from __future__ import annotations

import datetime as dt
import re

from .. import util
from ..http import Fetcher

NAME = "wevity"
LABEL = "위비티"
LIST_URL = "https://www.wevity.com/?c=find&s=1&gub=1&gp={page}"
DETAIL_URL = "https://www.wevity.com/?c=find&s=1&gbn=view&ix={key}"

ROW = re.compile(
    r'<li[^>]*>\s*(?:(?!</li>).)*?<div class="tit">\s*'
    r'<a href="[^"]*?ix=(\d+)"[^>]*>(.*?)</a>\s*'
    r'<div class="sub-tit">(.*?)</div>\s*'
    r"</div>\s*"
    r'<div class="organ">(.*?)</div>\s*'
    r'<div class="day">\s*(.*?)\s*<span class="dday\s*([a-z]+)"[^>]*>(.*?)</span>\s*</div>\s*'
    r'<div class="read">(.*?)</div>',
    re.S,
)


def parse_list(html: str, base: dt.date | None = None) -> list[dict]:
    """목록 HTML → 레코드 리스트 (네트워크 없음)."""
    records: list[dict] = []
    for key, title, field, host, badge, _cls, status_raw, views in ROW.findall(html):
        name = util.clean(re.sub(r"\s+(SPECIAL|신규|IDEA)$", "", util.clean(title)))
        if not name:
            continue
        records.append(
            {
                "source": NAME,
                "key": key,
                "name": name,
                "host": util.clean(host),
                "url": DETAIL_URL.format(key=key),
                "field": util.clean(field).removeprefix("분야 : ").removeprefix("분야:").strip(),
                "apply_start": None,
                "apply_end": util.wevity_deadline(badge, base),
                "apply_end_basis": "badge",
                "status_raw": util.clean(status_raw),
                "views": util.to_int(views),
                "region": None,
                "eligibility": None,
                "prize": None,
            }
        )
    return records


def fetch(fetcher: Fetcher, *, max_pages: int = 5, base: dt.date | None = None) -> list[dict]:
    """목록을 ``max_pages`` 페이지까지 수집한다(마감된 행은 버린다)."""
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        html = fetcher.get(LIST_URL.format(page=page))
        rows = parse_list(html, base)
        if not rows:
            break
        for row in rows:
            if row["status_raw"] == "마감":
                continue
            seen.setdefault(row["key"], row)
    return list(seen.values())
