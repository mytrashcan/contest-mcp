from __future__ import annotations

import datetime as dt

from contest_mcp import detail, ics

BASE = dt.date(2026, 11, 12)

HTML = """
<html><head>
<title>매경 AI 광고 공모전 | 위비티</title>
<meta name="description" content="공모 대상 : 전국 대학생 / 접수기간 : 2026.11.12 ~ 2026.12.05 / 시상내역 : 대상 500만원 / 문의처 : 02-123-4567">
<meta property="og:title" content="[제3회] 매경 AI 광고 공모전">
</head><body>
<div>지원 자격 : 국내 대학 재학생 및 휴학생</div>
<div>배점 : 창의성 30, 실현가능성 30, 완성도 40</div>
<div>제출물 : 30초 영상 1편, 기획서 5쪽</div>
<div>본선 발표 2026.12.12 대면 필참</div>
</body></html>
"""


def test_parse_extracts_summary_and_deadline():
    parsed = detail.parse(HTML, "https://example.com/1", BASE)
    assert "전국 대학생" in parsed["summary"]
    assert parsed["deadline_from_detail"] == "2026-12-05"
    assert parsed["title"].startswith("[제3회]")


def test_parse_finds_labeled_fields_with_evidence():
    parsed = detail.parse(HTML, "https://example.com/1", BASE)
    fields = parsed["fields"]
    assert "eligibility" in fields and "대학 재학생" in fields["eligibility"]["evidence"]
    assert "시상" in fields["award"]["evidence"]
    assert "창의성" in fields["scoring"]["evidence"]
    assert "영상" in fields["submission"]["evidence"]


def test_parse_reports_missing_instead_of_inventing():
    parsed = detail.parse(HTML, "https://example.com/1", BASE)
    assert "contact" not in parsed["missing"]  # meta에 문의처가 있다
    assert "prize_people" in parsed["missing"]  # 시상 인원은 원문에 없다


def test_parse_collects_date_mentions():
    parsed = detail.parse(HTML, "https://example.com/1", BASE)
    dates = [mention["date"] for mention in parsed["date_mentions"]]
    assert "2026-12-12" in dates


def test_ics_is_crlf_folded_and_counts_events():
    events = [
        {"date": "2026-12-05", "summary": "[마감] 매경 AI 광고 공모전", "description": "매경미디어 · D-23", "url": "https://example.com/1"},
        {"date": "2026-12-12", "summary": "[발표] 본선", "description": "대면 필참", "url": ""},
        {"date": None, "summary": "[확인] 날짜 미정"},
    ]
    blob = ics.build(events, alarm_days=2, stamp=BASE)
    raw = blob.encode("utf-8")
    assert raw.count(b"BEGIN:VEVENT") == 2  # 날짜 없는 이벤트는 만들지 않는다
    assert b"\n" not in raw.replace(b"\r\n", b"")  # CRLF만
    assert b"DTSTART;VALUE=DATE:20261205" in raw
    assert b"TRIGGER:-P2D" in raw
    assert all(len(line.encode("utf-8")) <= 75 for line in blob.split("\r\n"))


# 콘테스트코리아 실제 구조를 줄인 픽스처: 사이트 껍데기(콘코알림·주메뉴·추천 공모전 목록)가
# 본문과 같은 텍스트로 섞여 들어온다. 껍데기의 날짜는 이 공고의 발표일이 아니다.
CK_HTML = """
<html><head>
<title>2026 사회보장 AI 혁신 아이디어 경진대회</title>
<meta name="description" content="{dump}">
</head><body>
<div class="top_search_wrap">
  <div class="topNotice"><span class="icon">콘코알림</span>
    <div class="topNotice_slide">
      <div class="list"><a href="/sub/view.php?str_no=1">[발표 11/27일로 연기] 문화·관광·콘텐츠 분야 대국민 공모전</a></div>
      <div class="list"><a href="/files/meet/1.jpg">[수상자 발표] 삼성전자 AI HOME 인테리어 콘테스트</a></div>
    </div>
  </div>
</div>
<div class="view_cont_area">
  <div class="txt_area">
    <table>
      <tr><th scope="row">주최 . 주관</th><td>대구가톨릭대학교, 한국사회보장정보원</td></tr>
      <tr><th scope="row">접수기간</th><td>2026.10.21 ~ 2026.11.20</td></tr>
      <tr><th scope="row">심사기간</th><td>2026.11.25</td></tr>
      <tr><th scope="row">대회지역</th><td>대 구</td></tr>
      <tr><th scope="row">시상내역</th><td>상금(총상금 : 2,450만원 / 1위 : 500만원)</td></tr>
    </table>
    <div class="cont">- 제출 서류를 하나의 ZIP 파일로 압축해 제출합니다. (ax@wma.co.kr)
    - 서류심사: 2026년 11월 23일 (월) - 본선 발표평가 및 시상식: 2026년 12월 3일 (목)</div>
  </div>
</div>
<div class="list_style_2"><ul><li class="imminent">
  <a href="/sub/view.php?str_no=9">The 6th NN Arts 전국 입시생 성악 콩쿠르</a>
  <div class="date"><span class="step-2"><em>심사</em> 12.12 </span><span><em>발표</em> 12.21 </span></div>
</li></ul></div>
</body></html>
"""

# 실측: 콘테스트코리아·위비티의 meta description 은 본문 전체를 잘라 넣은 덤프다(815~2216자).
CK_DUMP = ("2026 사회보장 데이터를 활용한 AI 혁신 아이디어 경진대회 - 공동 주최 : 대구가톨릭대학교, "
           "한국사회보장정보원 - 후원 : 보건복지부전 국민 누구나 개인 또는 팀으로 참여 가능 - "
           "팀 구성 인원 제한 없음 - 개인 또는 팀 당 1개 아이디어 제출 가능 - 사회보장 데이터와 AI를 "
           "활용한 사회보장 분야 혁신 아이디어 - 창업·서비스 혁신 - 복지행정·민원 개선 - 연구 아이디어 - "
           "기타 사회보장 분야의 문제해결 및 국민 편익 증진을 위한 혁신 아이디어2026 10월 21일 (수) ~ "
           "11월 20일 (금) - 홈페이지에서 공고문과 제출 양식을 내려받습니다. - 지정 양식과 서명 기준에 맞춰 "
           "제출 서류를 작성합니다. - 제출 서류를 하나의 ZIP 파일로 압축하여 운영사무국 이메일로 제출합니다. "
           "- 이메일 제목 및 파일명 : AI혁신 경진대회_팀명(신청인원) - 참가신청서(PDF) - 아이디어 제안서(HWP) "
           "- 청렴서약서(PDF) - 개인정보 수집·이용 동의서(PDF)")


def test_chrome_blocks_do_not_leak_other_contests_dates():
    parsed = detail.parse(CK_HTML.format(dump=CK_DUMP), "https://example.com/ck", BASE)
    dates = [mention["date"] for mention in parsed["date_mentions"]]
    # 껍데기에 있던 다른 공고 날짜는 잡히면 안 된다.
    assert "2026-11-27" not in dates  # 콘코알림의 '[발표 11/27]'
    assert "2026-12-12" not in dates  # 추천 공모전 목록의 심사일
    assert "2026-12-21" not in dates  # 추천 공모전 목록의 발표일
    # 본문에 실제로 적힌 날짜는 남는다.
    assert "2026-11-23" in dates
    assert "2026-12-03" in dates


def test_dumped_meta_description_is_not_used_as_summary():
    parsed = detail.parse(CK_HTML.format(dump=CK_DUMP), "https://example.com/ck", BASE)
    assert parsed["summary"] == ""


def test_table_rows_win_over_body_text_and_region_is_normalized():
    parsed = detail.parse(CK_HTML.format(dump=CK_DUMP), "https://example.com/ck", BASE)
    assert parsed["fields"]["award"]["evidence"].startswith("시상내역 상금")
    assert parsed["fields"]["award"]["basis"] == "table"
    assert parsed["region"] == "대구"
    assert parsed["deadline_from_detail"] == "2026-11-20"


def test_date_mentions_are_deduplicated():
    parsed = detail.parse(CK_HTML.format(dump="접수기간 2026.10.21 ~ 2026.11.20"), "https://example.com/ck", BASE)
    pairs = [(mention["date"], mention["label"]) for mention in parsed["date_mentions"]]
    assert len(pairs) == len(set(pairs))


def test_evidence_stops_at_the_next_label():
    html = """<html><body><div>제출서류 : 기획서 3쪽 / 심사기준 : 창의성 40, 완성도 60</div></body></html>"""
    parsed = detail.parse(html, "https://example.com/x", BASE)
    assert "심사기준" not in parsed["fields"]["submission"]["evidence"]
    assert "창의성" in parsed["fields"]["scoring"]["evidence"]
