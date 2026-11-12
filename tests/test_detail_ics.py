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
