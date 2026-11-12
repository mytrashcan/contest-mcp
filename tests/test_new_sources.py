"""신규 소스(링커리어·콘테스트코리아) 파서를 고정 픽스처로 검증."""

from __future__ import annotations

import datetime as dt
import json

from contest_mcp.sources import contestkorea, linkareer

BASE = dt.date(2026, 11, 12)

LINKAREER_JSON = {
    "pageProps": {
        "activityItems": [
            {"url": "https://linkareer.com/activity/364007", "name": "제1회 순천시 청년정책 숏폼 공모전", "imageUrl": ""},
            {"url": "https://linkareer.com/activity/363900", "name": "온라인 방탈출 캠페인", "imageUrl": ""},
        ],
        "__APOLLO_STATE__": {
            "Activity:364007": {
                "__typename": "Activity",
                "id": 364007,
                "title": "제1회 순천시 청년정책 숏폼 공모전",
                "organizationName": "순천시",
                "recruitCloseAt": 1795013999999,  # 2026-11-18 (KST)
                "viewCount": 1234,
                "activityTypeID": 3,
            },
            "Activity:363900": {
                "__typename": "Activity",
                "id": 363900,
                "title": "온라인 방탈출 캠페인",
                "organizationName": "대전중구중독관리통합지원센터",
                "recruitCloseAt": 1795791599999,  # 2026-11-27
                "viewCount": 30,
            },
        },
    }
}

CONTESTKOREA_HTML = """
<ul class="list">
  <li class="imminent">
    <div class="title">
      <a href="/sub/view.php?int_gbn=1&Txt_bcode=030310001&str_no=202610280017">
        <span class="category">학문•과학•IT</span>
        <span class="category">The 다양한 분야</span>
        <span class="txt">2026 제18회 전국청소년 모형항공기대회</span>
      </a>
    </div>
    <ul class="host">
      <li>주최 . 사천시, 한국폴리텍대학 항공 캠퍼스</li>
      <li>대상 . 초등학생, 중학생</li>
    </ul>
    <div class="d-day orange">
      <span class="day">D-2</span>
      <span class="condition">마감임박</span>
    </div>
  </li>
  <li>
    <div class="title">
      <a href="/sub/view.php?int_gbn=1&Txt_bcode=030310001&str_no=202611020040">
        <span class="category">학문•과학•IT</span>
        <span class="txt">2027 동계 청소년 IT학술대회</span>
      </a>
    </div>
    <ul class="host">
      <li>주최 . 한국정보과학회</li>
    </ul>
    <div class="d-day green">
      <span class="day">D-45</span>
      <span class="condition">접수중</span>
    </div>
  </li>
</ul>
"""


def test_linkareer_parses_epoch_deadline():
    rows = linkareer.parse_data(json.dumps(LINKAREER_JSON), BASE)
    assert len(rows) == 2
    first = rows[0]
    assert first["key"] == "364007"
    assert first["name"] == "제1회 순천시 청년정책 숏폼 공모전"
    assert first["host"] == "순천시"
    assert first["apply_end"] == "2026-11-18"
    assert first["views"] == 1234
    assert first["url"] == "https://linkareer.com/activity/364007"


def test_linkareer_build_id_extraction():
    html = '<script id="__NEXT_DATA__" type="application/json">{"buildId":"linkareer-client-1794465086581"}</script>'
    assert linkareer.build_id_from_html(html) == "linkareer-client-1794465086581"
    assert linkareer.build_id_from_html("<html></html>") is None


def test_contestkorea_parses_rows_without_dday_offset():
    rows = contestkorea.parse_list(CONTESTKOREA_HTML, BASE)
    assert len(rows) == 2
    first = rows[0]
    assert first["key"] == "202610280017"
    assert first["name"] == "2026 제18회 전국청소년 모형항공기대회"
    assert first["host"].startswith("사천시")
    assert first["field"] == "학문•과학•IT, The 다양한 분야"
    # 콘테스트코리아 D-2는 오늘+2 (위비티와 달리 보정하지 않는다)
    assert first["apply_end"] == "2026-11-14"
    assert first["status_raw"] == "마감임박"
    assert rows[1]["apply_end"] == "2026-12-27"
