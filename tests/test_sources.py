"""각 소스 파서를 고정 HTML/JSON 픽스처로 검증 (네트워크 없음)."""

from __future__ import annotations

import datetime as dt
import json

from contest_mcp.sources import allcon, thinkgood, wevity

BASE = dt.date(2026, 11, 12)

WEVITY_HTML = """
<ul class="list">
  <li class="top">
    <div class="tit">공모전명</div><div class="organ">주최사</div><div class="day">현재현황</div><div class="read">조회수</div>
  </li>
  <li >
    <div class="tit">
      <a href="?c=find&s=1&gub=1&gbn=view&gp=1&ix=112073">[제3회] 매경 AI 광고 공모전  <span class='stat spec'>SPECIAL</span> </a>
      <div class="sub-tit">분야 : 기획/아이디어, 웹/모바일/IT</div>
    </div>
    <div class="organ">매경미디어</div>
    <div class="day"> D-18 <span class="dday ing">접수중</span> </div>
    <div class="read">47,160</div>
  </li>
  <li  class='bg'>
    <div class="tit">
      <a href="?c=find&s=1&gub=1&gbn=view&gp=1&ix=111999">이미 끝난 공모전</a>
      <div class="sub-tit">분야 : 문학/학술</div>
    </div>
    <div class="organ">어떤기관</div>
    <div class="day"> D-1 <span class="dday end">마감</span> </div>
    <div class="read">123</div>
  </li>
</ul>
"""

THINKGOOD_JSON = {
    "totalcnt": 2,
    "listJsonData": [
        {
            "contest_pk": 112863,
            "program_nm": "2026 부산 해양문학 공모전",
            "host_company": "한국해양진흥공사",
            "accept_dt": "2026-11-04 19:00:00.0",
            "finish_dt": "2026-11-17 15:00:00.0",
            "process_nm": "마감임박",
            "enter_qualified_nm": "초등학생, 중학생",
            "prize_money": "500만원",
        },
        {
            "contest_pk": 112999,
            "program_nm": "지난 공모전",
            "host_company": "어떤기관",
            "accept_dt": "2026-09-01 00:00:00.0",
            "finish_dt": "2026-10-01 00:00:00.0",
            "process_nm": "마감",
            "enter_qualified_nm": "제한없음",
            "prize_money": "",
        },
    ],
}

ALLCON_JSON = {
    "rows": [
        {
            "cl_srl": "541883",
            "cl_title": "<a href='/hit/contest/4490?page=1'>2027 KT&G 국제 대학생 창업교류전(ASVF)</a>",
            "cl_cate": '<span class="badge cl_cate">아이디어·창업</span><span class="badge cl_cate">대학생</span>',
            "cl_status": "<span class='badge cl_status2'>접수중</span><br>D-29",
            "cl_view": "14503",
            "cl_date": "26.08.10~26.12.11",
            "cl_host": "한국경제신문",
        }
    ]
}


def test_wevity_parses_rows_and_skips_header():
    rows = wevity.parse_list(WEVITY_HTML, BASE)
    assert len(rows) == 2
    first = rows[0]
    assert first["key"] == "112073"
    assert first["name"] == "[제3회] 매경 AI 광고 공모전"  # SPECIAL 배지 제거
    assert first["host"] == "매경미디어"
    assert first["field"] == "기획/아이디어, 웹/모바일/IT"
    assert first["views"] == 47160
    assert first["apply_end"] == "2026-11-29"  # D-18 → 11/30 에서 하루 당김
    assert first["status_raw"] == "접수중"
    assert rows[1]["status_raw"] == "마감"


def test_thinkgood_parses_json_fields():
    rows = thinkgood.parse_response(json.dumps(THINKGOOD_JSON), BASE)
    assert len(rows) == 2
    first = rows[0]
    assert first["key"] == "112863"
    assert first["apply_start"] == "2026-11-04"
    assert first["apply_end"] == "2026-11-17"
    assert first["eligibility"] == "초등학생, 중학생"
    assert first["prize"] == "500만원"
    assert "contest_pk=112863" in first["url"]


def test_allcon_parses_badges_and_period():
    rows = allcon.parse_rows(json.dumps(ALLCON_JSON), BASE)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"].startswith("2027 KT&G")
    assert row["url"].startswith("https://www.all-con.co.kr/hit/contest/4490")
    assert row["field"] == "아이디어·창업"
    assert row["eligibility"] == "대학생"
    assert row["views"] == 14503
    assert (row["apply_start"], row["apply_end"]) == ("2026-08-10", "2026-12-11")
    assert row["status_raw"] == "접수중"
