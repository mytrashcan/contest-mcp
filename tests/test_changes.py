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


def test_extension_is_detected_after_detail_promotion(tmp_path):
    """상세를 열어 basis=detail이 된 뒤에도, 소스가 날짜를 옮기면 연장으로 잡는다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-19", basis="badge")], BASE)
    db.save_detail(conn, "wevity:1", {"fields": {}}, apply_end="2026-11-20", base=BASE)

    # 목록 값이 그대로면 상세 날짜를 유지하고 변경으로 보지 않는다
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-19", basis="badge")], BASE)
    assert db.changes(conn, days=7, base=BASE) == []
    assert db.get(conn, "wevity:1", BASE)["apply_end"] == "2026-11-20"

    # 소스가 날짜를 옮기면 '보이던 값 → 새 값'으로 기록하고 상세 캐시를 만료시킨다
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-26", basis="badge")], BASE)
    rows = db.changes(conn, days=7, base=BASE)
    assert [(row["old_value"], row["new_value"]) for row in rows] == [("2026-11-20", "2026-11-26")]
    item = db.get(conn, "wevity:1", BASE)
    assert item["apply_end"] == "2026-11-26"
    assert item["detail_checked_at"] is None


def test_list_catching_up_to_detail_date_is_not_a_change(tmp_path):
    """목록이 상세 원문 날짜로 뒤늦게 맞춰진 것은 변경이 아니다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-19", basis="badge")], BASE)
    db.save_detail(conn, "wevity:1", {"fields": {}}, apply_end="2026-11-20", base=BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20", basis="badge")], BASE)
    assert db.changes(conn, days=7, base=BASE) == []
    assert db.get(conn, "wevity:1", BASE)["detail_checked_at"] == BASE.isoformat()


def test_old_db_without_list_deadline_column_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE contest (id TEXT PRIMARY KEY, source TEXT NOT NULL, key TEXT NOT NULL, "
        "group_id TEXT NOT NULL, name TEXT NOT NULL, host TEXT, url TEXT, field TEXT, apply_start TEXT, "
        "apply_end TEXT, apply_end_basis TEXT, status_raw TEXT, views INTEGER, eligibility TEXT, "
        "region TEXT, prize TEXT, detail_json TEXT, detail_at TEXT, first_seen TEXT, last_seen TEXT)"
    )
    raw.commit()
    raw.close()

    conn = db.connect(path)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-27")], BASE)
    assert len(db.changes(conn, days=7, base=BASE)) == 1


def test_submitted_flag_survives_representative_change(tmp_path):
    """제출 기록이 그룹의 대표가 아닌 행에 있어도 already_submitted 는 참이어야 한다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "A 공모전", "2026-11-20", basis="badge"),
            _rec("thinkgood", "9", "A 공모전", "2026-11-21", basis="list"),
        ],
        BASE,
    )
    db.set_state(conn, "wevity:1", "submitted", base=BASE)
    items = db.flag_submitted(conn, db.search(conn, dday_max=30, base=BASE))
    assert len(items) == 1
    assert items[0]["id"] == "thinkgood:9"  # 근거 순위가 높은 행이 대표
    assert items[0]["already_submitted"] is True
