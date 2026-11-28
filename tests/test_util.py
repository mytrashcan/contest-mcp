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


def test_wevity_dday_zero_is_not_shifted():
    """D-0은 D-DAY와 같은 뜻이라 하루 당기면 당일 마감 공고가 '마감'으로 표시된다."""
    assert util.wevity_deadline("D-0", BASE) == "2026-11-12"
    assert util.wevity_deadline("D - 0", BASE) == "2026-11-12"
    assert util.wevity_deadline("D-DAY", BASE) == "2026-11-12"
    assert util.status_of(None, util.wevity_deadline("D-0", BASE), BASE) == "open"


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


def _fake_datetime_at(year: int):
    """`datetime.datetime` 대역 — `.now()` 가 지정한 연도의 고정 시각을 돌려준다."""
    real = dt.datetime
    stamp = real(year, 6, 15, 12, 0, 0)

    class FakeDateTime(real):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz=None):
            return stamp

    return FakeDateTime


def test_parse_period_end_year_comes_from_start_not_today(monkeypatch):
    """꼬리에 연도가 없을 때 '오늘'의 연도를 쓰면 시간이 흐른 뒤 결과가 달라진다.

    2026-11-28 회귀: 2030년에 같은 문자열을 넣으면 끝이 2030-01-05가 됐다(= 해를 넘긴 기간이 아님).
    """
    for year in (2026, 2030, 2035):
        monkeypatch.setattr(util.dt, "datetime", _fake_datetime_at(year))
        assert util.parse_period("2026.12.20~01.05") == ("2026-12-20", "2027-01-05"), year
    monkeypatch.undo()


def test_parse_period_uses_given_base_for_both_ends():
    # 명시 base 가 있으면 연도 생략 꼬리는 base 연도로 읽는다(기존 동작 보존)
    assert util.parse_period("11.02~11.16", dt.date(2026, 10, 1)) == ("2026-11-02", "2026-11-16")
    # 시작을 못 읽으면 꼬리만 돌려준다
    assert util.parse_period("미정~11.16", dt.date(2026, 10, 1)) == (None, "2026-11-16")
