"""링커리어(linkareer.com) — Next.js 데이터 라우트.

목록 HTML에는 카드가 없고 ``buildId`` 만 있다. 그 값으로
``/_next/data/<buildId>/list/contest.json?filterBy=ALL&page=N`` 을 호출하면
``activityItems``(id·이름)와 ``__APOLLO_STATE__`` 의 ``Activity:*``(주최·마감·조회수)가 함께 온다.
마감일이 epoch(ms)라 위비티 같은 D-day 보정이 필요 없다.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from .. import util
from ..http import Fetcher

NAME = "linkareer"
LABEL = "링커리어"
LIST_HTML = "https://linkareer.com/list/contest?filterBy=ALL"
DATA_URL = "https://linkareer.com/_next/data/{build_id}/list/contest.json?filterBy=ALL&page={page}"
DETAIL_URL = "https://linkareer.com/activity/{key}"

BUILD_ID = re.compile(r'buildId":"([^"]+)"')


def build_id_from_html(html: str) -> str | None:
    match = BUILD_ID.search(html)
    return match.group(1) if match else None


def _date_from_ms(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return dt.datetime.fromtimestamp(value / 1000, util.SEOUL).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def parse_data(payload: dict | str, base: dt.date | None = None) -> list[dict]:
    """데이터 라우트 JSON → 레코드 리스트 (네트워크 없음)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    page_props = data.get("pageProps") or {}
    items = page_props.get("activityItems") or []
    apollo = page_props.get("__APOLLO_STATE__") or {}

    records: list[dict] = []
    for item in items:
        url = item.get("url") or ""
        key = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        if not key:
            continue
        detail = apollo.get(f"Activity:{key}") or {}
        records.append(
            {
                "source": NAME,
                "key": key,
                "name": util.clean(item.get("name") or detail.get("title")),
                "host": util.clean(detail.get("organizationName")),
                "url": DETAIL_URL.format(key=key),
                "field": None,
                "apply_start": None,
                "apply_end": _date_from_ms(detail.get("recruitCloseAt")),
                "apply_end_basis": "list",
                "status_raw": None,
                "views": util.to_int(detail.get("viewCount")),
                "region": None,
                "eligibility": None,
                "prize": None,
            }
        )
    return records


def fetch(fetcher: Fetcher, *, max_pages: int = 5, base: dt.date | None = None) -> list[dict]:
    build_id = build_id_from_html(fetcher.get(LIST_HTML))
    if not build_id:
        raise RuntimeError("링커리어 buildId를 찾지 못했습니다(페이지 구조 변경 의심)")
    ref = base or util.today()
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        raw = fetcher.get(DATA_URL.format(build_id=build_id, page=page))
        rows = parse_data(raw, ref)
        if not rows:
            break
        for row in rows:
            if row["apply_end"] and row["apply_end"] < ref.isoformat():
                continue
            seen.setdefault(row["key"], row)
    return list(seen.values())
