"""변경 감지(change_log)와 조회수 정렬 검증."""

from __future__ import annotations

import datetime as dt

from contest_mcp import db

BASE = dt.date(2026, 11, 12)


def _rec(source, key, name, end, **extra):
    return {
        "source": source,
        "key": key,
        "name": name,
        "host": extra.get("host", "주최"),
        "url": extra.get("url", f"https://example.com/{source}/{key}"),
        "apply_start": extra.get("apply_start"),
        "apply_end": end,
        "apply_end_basis": extra.get("basis", "list"),
        "status_raw": extra.get("status_raw", "접수중"),
        "views": extra.get("views"),
        "field": None,
        "eligibility": None,
        "region": None,
        "prize": extra.get("prize"),
    }


def test_deadline_extension_is_logged(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-27")], BASE)
    rows = db.changes(conn, days=7, base=BASE)
    assert len(rows) == 1
    assert rows[0]["field"] == "apply_end"
    assert (rows[0]["old_value"], rows[0]["new_value"]) == ("2026-11-20", "2026-11-27")
    assert rows[0]["name"] == "A 공모전"


def test_prize_and_title_changes_are_logged(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20", prize="대상 100만원")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전(명칭 변경)", "2026-11-20", prize="대상 500만원")], BASE)
    fields = {row["field"] for row in db.changes(conn, days=7, base=BASE)}
    assert fields == {"name", "prize"}


def test_same_transition_is_not_logged_twice(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-27")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], BASE)  # 되돌림
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-27")], BASE)  # 같은 전이 반복
    rows = db.changes(conn, days=7, base=BASE)
    assert len([row for row in rows if row["field"] == "apply_end"]) == 2  # 20→27, 27→20 각 1건


def test_view_count_changes_are_not_logged(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20", views=100)], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20", views=900)], BASE)
    assert db.changes(conn, days=7, base=BASE) == []


def test_detail_deadline_is_not_flagged_as_change(tmp_path):
    """상세 원문 날짜가 배지 날짜를 이기는 것은 '변경'이 아니라 정정이다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-24", basis="badge")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-25", basis="detail")], BASE)
    rows = db.changes(conn, days=7, base=BASE)
    # 기록은 남기되 basis='detail'로 표시해 승격임을 구분할 수 있게 한다
    assert len(rows) == 1 and rows[0]["basis"] == "detail"


def test_changes_window_filters_old_entries(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], dt.date(2026, 10, 1))
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-27")], dt.date(2026, 10, 2))
    assert db.changes(conn, days=7, base=BASE) == []
    assert len(db.changes(conn, days=60, base=BASE)) == 1


def test_sort_views_low_puts_low_view_contests_first(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "인기 공모전", "2026-11-20", views=47000),
            _rec("wevity", "2", "한산한 공모전", "2026-11-25", views=42),
            _rec("wevity", "3", "조회 미확인 공모전", "2026-11-15"),
        ],
        BASE,
    )
    order = [item["name"] for item in db.search(conn, dday_max=30, sort="views_low", base=BASE)]
    assert order == ["한산한 공모전", "인기 공모전", "조회 미확인 공모전"]
    high = [item["name"] for item in db.search(conn, dday_max=30, sort="views_high", base=BASE)]
    assert high[0] == "인기 공모전"
