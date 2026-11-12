"""씽굿(thinkcontest.com) — 목록 POST JSON API.

브라우저가 필요 없다. ``subList.do`` 에 JSON 본문을 POST하면 ``listJsonData`` 배열이 온다.
행에 자격(``enter_qualified_nm``)·상금(``prize_money``)·현황(``process_nm``)이 들어 있어
목록 단계에서 이미 조건 판정에 필요한 필드를 확보한다.
"""

from __future__ import annotations

import datetime as dt
import json

from .. import util
from ..http import Fetcher

NAME = "thinkgood"
LABEL = "씽굿"
API = "https://www.thinkcontest.com/thinkgood/user/contest/subList.do"
DETAIL_URL = "https://www.thinkcontest.com/thinkgood/user/contest/view.do?contest_pk={key}"

BODY = {
    "recordsPerPage": 50,
    "currentPageNo": 1,
    "contest_field": "",
    "host_organ": "",
    "enter_qualified": "",
    "award_size": "",
    "searchStatus": "Y",
    "sidx": "",
    "sord": "",
}


def parse_response(payload: dict | str, base: dt.date | None = None) -> list[dict]:
    """API 응답(JSON 문자열 또는 dict) → 레코드 리스트 (네트워크 없음)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    rows = data.get("listJsonData") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    records: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or "program_nm" not in row:
            continue
        status_raw = util.clean(row.get("process_nm"))
        records.append(
            {
                "source": NAME,
                "key": str(row.get("contest_pk")),
                "name": util.clean(row.get("program_nm")),
                "host": util.clean(row.get("host_company")),
                "url": DETAIL_URL.format(key=row.get("contest_pk")),
                "field": util.clean(row.get("contest_field_nm")),
                "apply_start": util.parse_date(row.get("accept_dt"), base),
                "apply_end": util.parse_date(row.get("finish_dt"), base),
                "apply_end_basis": "list",
                "status_raw": status_raw,
                "views": util.to_int(row.get("view_cnt") or row.get("hits")),
                "region": None,
                "eligibility": util.clean(row.get("enter_qualified_nm")) or None,
                "prize": util.clean(row.get("prize_money") or row.get("award_size_nm")) or None,
            }
        )
    return records


def fetch(fetcher: Fetcher, *, max_pages: int = 5, base: dt.date | None = None) -> list[dict]:
    seen: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        body = {**BODY, "currentPageNo": page}
        raw = fetcher.post_json(API, body)
        rows = parse_response(raw, base)
        if not rows:
            break
        for row in rows:
            if row["status_raw"] == "마감":
                continue
            seen.setdefault(row["key"], row)
    return list(seen.values())
