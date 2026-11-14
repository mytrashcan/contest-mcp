"""올콘(all-con.co.kr) — AJAX 목록 JSON.

``ajax.contest_list.php`` 에 폼 POST + ``X-Requested-With`` 헤더가 필요하다.
``t=1`` 공모전 / ``t=2`` 대외활동. 카테고리 배지가 [분야, 자격...] 순서로 붙어 온다.

주의: 이 호스트는 인증서 체인이 불완전해 ``http.Fetcher`` 가 검증을 끄고 수집하며,
그 사실을 경고로 남긴다.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from .. import util
from ..http import Fetcher

NAME = "allcon"
LABEL = "올콘"
API = "https://www.all-con.co.kr/page/ajax.contest_list.php"
BASE = "https://www.all-con.co.kr"

_TITLE = re.compile(r"<a href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", re.S)
_BADGE = re.compile(r'<span class="badge cl_cate">(.*?)</span>', re.S)
_STATUS = re.compile(r"<span class='badge cl_status2'>(.*?)</span>", re.S)
_STATUS_ALT = re.compile(r'<span class="badge cl_status2">(.*?)</span>', re.S)
_DDAY = re.compile(r"D\s*-\s*\d{1,3}")


def parse_rows(payload: dict | str, base: dt.date | None = None) -> list[dict]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    rows = data.get("rows") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    records: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title_match = _TITLE.search(row.get("cl_title") or "")
        if not title_match:
            continue
        href, raw_title = title_match.groups()
        badges = [util.clean(b) for b in _BADGE.findall(row.get("cl_cate") or "")]
        status_match = _STATUS.search(row.get("cl_status") or "") or _STATUS_ALT.search(row.get("cl_status") or "")
        status_raw = util.clean(status_match.group(1)) if status_match else ""
        start, end = util.parse_period(row.get("cl_date"), base)
        records.append(
            {
                "source": NAME,
                "key": str(row.get("cl_srl")),
                "name": util.clean(raw_title),
                "host": util.clean(row.get("cl_host")),
                "url": href if href.startswith("http") else BASE + href,
                "field": badges[0] if badges else None,
                "apply_start": start,
                "apply_end": end,
                "apply_end_basis": "list",
                "status_raw": status_raw,
                "views": util.to_int(row.get("cl_view")),
                "region": None,
                "eligibility": ", ".join(badges[1:]) if len(badges) > 1 else None,
                "prize": None,
            }
        )
    return records


def fetch(
    fetcher: Fetcher,
    *,
    max_pages: int = 5,
    base: dt.date | None = None,
    kinds: tuple[str, ...] = ("1",),
) -> list[dict]:
    seen: dict[str, dict] = {}
    for kind in kinds:
        for page in range(1, max_pages + 1):
            raw = fetcher.post_form(
                API,
                {"page": page, "t": kind, "sortname": "cl_order", "sortorder": "asc"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            rows = parse_rows(raw, base)
            if not rows:
                util.ensure_first_page(LABEL, page)
                break
            for row in rows:
                seen.setdefault(row["key"], row)
    return list(seen.values())
