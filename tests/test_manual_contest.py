"""소스에 없는 공고 직접 등록(mark --name)·출력 방어(마감일 없는 행)."""

from __future__ import annotations

import datetime as dt

import pytest

from contest_mcp import cli, db

BASE = dt.date(2026, 11, 16)


def _connect(tmp_path, monkeypatch):
    path = tmp_path / "t.sqlite"
    monkeypatch.setenv("CONTEST_MCP_DB", str(path))
    return db.connect(path)


def test_add_manual_contest_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    first = db.add_manual_contest(conn, "KEIT 대국민 혁신 아이디어 공모전", host="KEIT", base=BASE)
    second = db.add_manual_contest(conn, "KEIT 대국민 혁신 아이디어 공모전", base=BASE)
    assert first == second
    assert first.startswith("manual:")
    rows = conn.execute("SELECT name, source FROM contest").fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


def test_add_manual_contest_rejects_bad_date(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    with pytest.raises(ValueError):
        db.add_manual_contest(conn, "아무 공모전", apply_end="2026/11/30")


def test_mark_with_name_registers_and_returns_zero(tmp_path, monkeypatch, capsys):
    _connect(tmp_path, monkeypatch)
    code = cli.main([
        "mark",
        "submitted",
        "--name", "한국석유공사 대국민 혁신 아이디어 공모전",
        "--host", "한국석유공사",
        "--apply-end", "2026-11-27",
        "--submitted-at", "2026-11-09",
        "--channel", "email",
        "--result", "pending",
        "--result-note", "발표: 개별통보 추정",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "한국석유공사 대국민 혁신 아이디어 공모전 → submitted" in out

    assert cli.main(["my-list"]) == 0
    listed = capsys.readouterr().out
    assert "한국석유공사 대국민 혁신 아이디어 공모전" in listed
    # 발표일 메모가 텍스트 출력에 보인다
    assert "발표: 개별통보 추정" in listed


def test_mark_without_ident_or_name_fails_cleanly(monkeypatch, tmp_path, capsys):
    _connect(tmp_path, monkeypatch)
    code = cli.main(["mark", "submitted"])
    assert code == 2
    assert "공고 id 또는 --name" in capsys.readouterr().err


def test_deadlines_survives_row_without_date(tmp_path, monkeypatch):
    """마감일이 빈 문자열인 행이 있어도 종료코드 0 (출력 단계에서 죽지 않는다)."""
    conn = _connect(tmp_path, monkeypatch)
    db.upsert_many(conn, [{
        "source": "contestkorea",
        "key": "1",
        "name": "마감일 없는 공고",
        "host": "주최",
        "url": "https://example.com/1",
        "apply_end": None,
        "apply_end_basis": "list",
    }], BASE)
    conn.execute("UPDATE contest SET apply_end = ''")
    conn.commit()
    conn.close()

    assert cli.main(["deadlines", "--days", "30"]) == 0


def test_dday_label_handles_missing_date():
    assert cli._dday_label({"dday": 3}) == "D-3"
    assert cli._dday_label({"dday": None}) == "D-?"
    assert cli._dday_label({}) == "D-?"
