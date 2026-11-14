"""콘테스트코리아(contestkorea.com) — 서버 렌더 목록.

행이 ``<div class="title">`` 단위로 끊기므로 그 경계로 자른다. 각 구간에
분야(span.category) · 제목(span.txt) · 주최(ul.host) · 현황(span.day / span.condition)이 모두 있다.

**D-day 규칙이 위비티와 반대다.** 실측: 배지 D-2 행의 상세 원문 접수기간이 2026.11.14(오늘+2)라
위비티처럼 -1일 보정하면 하루 이른 날짜가 된다 → 이 소스는 **보정하지 않는다**.
"""

from __future__ import annotations

import datetime as dt
import re

from .. import util
from ..http import Fetcher

NAME = "contestkorea"
LABEL = "콘테스트코리아"
LIST_URL = "https://www.contestkorea.com/sub/list.php?int_gbn=1&Txt_bcode=030310001&page={page}"
DETAIL_URL = "https://www.contestkorea.com/sub/view.php?int_gbn=1&Txt_bcode=030310001&str_no={key}"

TITLE_BLOCK = '<div class="title">'
KEY = re.compile(r"str_no=(\d+)")
TEXT = re.compile(r'<span class="txt">(.*?)</span>', re.S)
CATEGORY = re.compile(r'<span class="category">(.*?)</span>', re.S)
HOST = re.compile(r'<ul class="host">(.*?)</ul>', re.S)
DAY = re.compile(r'<span class="day"[^>]*>(.*?)</span>', re.S)
CONDITION = re.compile(r'<span class="condition"[^>]*>(.*?)</span>', re.S)


def _host_from_block(block: str) -> str:
    match = HOST.search(block)
    if not match:
        return ""
    text = util.clean(match.group(1))
    text = re.sub(r"^(주최|주관|주최\s*[.·]\s*주관)\s*[.·]?\s*", "", text)
    return text.split("대상")[0].strip(" .·")


def parse_list(html: str, base: dt.date | None = None) -> list[dict]:
    """목록 HTML → 레코드 리스트 (네트워크 없음)."""
    ref = base or util.today()
    starts = [m.start() for m in re.finditer(re.escape(TITLE_BLOCK), html)]
    records: list[dict] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        block = html[start:end]
        key_match = KEY.search(block)
        title_match = TEXT.search(block)
        if not key_match or not title_match:
            continue
        categories = [util.clean(raw) for raw in CATEGORY.findall(block)]
        badge = DAY.search(block)
        condition = CONDITION.search(block)
        records.append(
            {
                "source": NAME,
                "key": key_match.group(1),
                "name": util.clean(title_match.group(1)),
                "host": _host_from_block(block),
                "url": DETAIL_URL.format(key=key_match.group(1)),
                "field": ", ".join(categories) or None,
                "apply_start": None,
                "apply_end": util.from_dday(badge.group(1), ref) if badge else None,
                "apply_end_basis": "badge",
                "status_raw": util.clean(condition.group(1)) if condition else None,
                "views": None,
                "region": None,
                "eligibility": None,
                "prize": None,
            }
        )
    return records


def fetch(fetcher: Fetcher, *, max_pages: int = 5, base: dt.date | None = None) -> list[dict]:
    ref = base or util.today()
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        rows = parse_list(fetcher.get(LIST_URL.format(page=page)), ref)
        if not rows:
            util.ensure_first_page(LABEL, page)
            break
        for row in rows:
            if (row["status_raw"] or "") == "마감":
                continue
            seen.setdefault(row["key"], row)
    return list(seen.values())
