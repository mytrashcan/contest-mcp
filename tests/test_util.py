from __future__ import annotations

import datetime as dt

from contest_mcp import util

BASE = dt.date(2026, 11, 12)


def test_clean_strips_tags_and_entities():
    assert util.clean("<div>가&amp;나&nbsp;다</div>") == "가&나 다"


def test_norm_name_ignores_year_brackets_and_punct():
    a = util.norm_name("[제3회] 매경 AI 광고 공모전")
    b = util.norm_name("매경 AI 광고 공모전 (2026)")
    assert a == b
    assert util.group_id("[제3회] 매경 AI 광고 공모전") == util.group_id("매경 AI 광고 공모전 (2026)")


def test_group_id_differs_for_different_names():
    assert util.group_id("A 공모전") != util.group_id("B 공모전")


def test_parse_date_formats():
    assert util.parse_date("2026-11-20") == "2026-11-20"
    assert util.parse_date("2026.11.20 15:00:00.0") == "2026-11-20"
    assert util.parse_date("26.11.20") == "2026-11-20"
    assert util.parse_date("2026년 11월 20일") == "2026-11-20"
    assert util.parse_date("11.20", BASE) == "2026-11-20"
    assert util.parse_date("말도 안 되는 값") is None


def test_parse_period_handles_two_digit_years():
    assert util.parse_period("26.08.10~26.12.11") == ("2026-08-10", "2026-12-11")


def test_parse_period_fills_missing_year_from_start():
    start, end = util.parse_period("2026.12.20~01.05")
    assert start == "2026-12-20"
    assert end == "2027-01-05"


def test_wevity_dday_is_shifted_one_day_earlier():
    # 위비티는 마감일 당일을 포함해 세므로 D-18 → 11/29(하루 당김)
    assert util.wevity_deadline("D-18", BASE) == "2026-11-29"
    assert util.wevity_deadline("D-DAY", BASE) == "2026-11-12"
    assert util.wevity_deadline("", BASE) is None


def test_status_of():
    assert util.status_of(None, "2026-11-20", BASE) == "open"
    assert util.status_of(None, "2026-11-01", BASE) == "closed"
    assert util.status_of("2026-11-20", "2026-12-01", BASE) == "upcoming"
    assert util.status_of(None, None, BASE) == "unknown"


def test_dday_of_signed():
    assert util.dday_of("2026-11-20", BASE) == 8
    assert util.dday_of("2026-11-01", BASE) == -11
    assert util.dday_of(None, BASE) is None


def test_to_int():
    assert util.to_int("47,160") == 47160
    assert util.to_int(None) is None


def test_clean_decodes_every_html_entity():
    assert util.clean("AI&#8226;데이터 &ldquo;공모전&rdquo;&#x2F;해커톤") == "AI•데이터 “공모전”/해커톤"
