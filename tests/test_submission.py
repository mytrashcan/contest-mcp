"""제출 이력 확장(mark 필드·증빙 조회·구버전 DB 마이그레이션)."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from contest_mcp import db

BASE = dt.date(2026, 11, 13)


def _rec(key, name="A 공모전"):
    return {
        "source": "wevity",
        "key": key,
        "name": name,
        "host": "주최",
        "url": f"https://example.com/{key}",
        "apply_end": "2026-11-20",
        "apply_end_basis": "list",
    }


def test_mark_records_submission_details(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("1")], BASE)
    db.set_state(
        conn,
        "wevity:1",
        "submitted",
        "메일 발송",
        BASE,
        submitted_at="2026-11-12",
        channel="email",
        proof="no",
        result="pending",
    )
    rows = db.my_list(conn, "submitted")
    assert len(rows) == 1
    row = rows[0]
    assert row["submitted_at"] == "2026-11-12"
    assert row["channel"] == "email"
    assert row["proof"] == "no"
    assert row["result"] == "pending"
    assert row["note"] == "메일 발송"


def test_mark_keeps_previous_fields_when_not_passed(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("1")], BASE)
    db.set_state(conn, "wevity:1", "submitted", base=BASE, submitted_at="2026-11-12", channel="email")
    db.set_state(conn, "wevity:1", "submitted", "결과 반영", base=BASE, result="won")
    row = db.my_list(conn, "submitted")[0]
    assert row["submitted_at"] == "2026-11-12"  # 유지
    assert row["channel"] == "email"  # 유지
    assert row["result"] == "won"
    assert row["note"] == "결과 반영"


def test_missing_proof_filters_only_unverified_submissions(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("1", "증빙 있음"), _rec("2", "증빙 없음"), _rec("3", "증빙 미확인"), _rec("4", "폐기")], BASE)
    db.set_state(conn, "wevity:1", "submitted", base=BASE, proof="yes")
    db.set_state(conn, "wevity:2", "submitted", base=BASE, proof="no")
    db.set_state(conn, "wevity:3", "submitted", base=BASE)
    db.set_state(conn, "wevity:4", "dropped", base=BASE)
    names = {row["name"] for row in db.my_list(conn, missing_proof=True)}
    assert names == {"증빙 없음", "증빙 미확인"}


def test_invalid_enum_values_are_rejected(tmp_path):
    """오타가 DB에 들어가면 '증빙 미확인' 필터가 조용히 틀어진다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("1")], BASE)
    with pytest.raises(ValueError):
        db.set_state(conn, "wevity:1", "submitted", base=BASE, proof="YES")
    with pytest.raises(ValueError):
        db.set_state(conn, "wevity:1", "submitted", base=BASE, channel="mail")
    with pytest.raises(ValueError):
        db.set_state(conn, "wevity:1", "submitted", base=BASE, result="win")


def test_old_database_gets_new_columns(tmp_path):
    """구버전 my_list(컬럼 4개)로 만든 DB도 열리면서 새 컬럼이 추가된다."""
    path = tmp_path / "old.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE my_list (contest_id TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT, updated_at TEXT NOT NULL);
        INSERT INTO my_list VALUES ('wevity:9', 'submitted', '예전 기록', '2026-10-01');
        """
    )
    raw.commit()
    raw.close()

    conn = db.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(my_list)")}
    assert {"submitted_at", "channel", "proof", "proof_path", "result", "result_note"} <= columns
    rows = db.my_list(conn, "submitted")
    assert rows[0]["note"] == "예전 기록"  # 기존 데이터 보존
