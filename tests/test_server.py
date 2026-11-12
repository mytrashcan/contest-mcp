"""서버 계약(툴 등록)과 refresh 배선을 네트워크 없이 검증."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

BASE = dt.date(2026, 11, 12)

EXPECTED_TOOLS = {"search_contests", "get_contest", "refresh", "deadlines", "mark", "my_list"}


def test_build_server_registers_expected_tools():
    pytest.importorskip("mcp")
    from contest_mcp import server

    instance = server.build_server()
    tools = asyncio.run(instance.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


def test_refresh_sources_writes_db_and_run_log(monkeypatch, tmp_path):
    from contest_mcp import db, server
    from contest_mcp.sources import wevity as wevity_module

    monkeypatch.setenv("CONTEST_MCP_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(
        wevity_module,
        "fetch",
        lambda fetcher, max_pages=5, base=None: [
            {
                "source": "wevity",
                "key": "1",
                "name": "가짜 공모전",
                "host": "테스트",
                "url": "https://example.com/1",
                "apply_end": "2026-11-20",
                "apply_end_basis": "badge",
                "status_raw": "접수중",
            }
        ],
    )
    report = server.refresh_sources(["wevity"], max_pages=1, base=BASE)
    assert report["sources"]["wevity"]["fetched"] == 1
    assert report["total"] == 1
    conn = db.connect(tmp_path / "db.sqlite")
    try:
        runs = db.runs(conn)
    finally:
        conn.close()
    assert runs[0]["source"] == "wevity" and runs[0]["fetched"] == 1


def test_refresh_sources_reports_unknown_source(monkeypatch, tmp_path):
    from contest_mcp import server

    monkeypatch.setenv("CONTEST_MCP_DB", str(tmp_path / "db.sqlite"))
    report = server.refresh_sources(["nope"], base=BASE)
    assert "알 수 없는 소스" in report["sources"]["nope"]["error"]
