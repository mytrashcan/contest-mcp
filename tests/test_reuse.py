"""재출품 조항 탐지 + 아이디어 유사도 경고."""

from __future__ import annotations

import datetime as dt

from contest_mcp import db, reuse

BASE = dt.date(2026, 11, 13)

CLAUSE_TEXT = (
    "응모작은 타 공모전 수상작이거나 이미 출품한 작품이면 심사에서 제외됩니다. "
    "시상내역: 대상 500만원"
)


def _seed(conn):
    db.upsert_many(
        conn,
        [
            {
                "source": "wevity",
                "key": "1",
                "name": "A 공모전",
                "host": "주최",
                "url": "https://example.com/1",
                "apply_end": "2026-11-20",
                "apply_end_basis": "list",
            }
        ],
        BASE,
    )


def test_detect_clauses_finds_patterns_with_evidence():
    clauses = reuse.detect_clauses(CLAUSE_TEXT)
    patterns = {clause["pattern"] for clause in clauses}
    assert {"타 공모전", "이미 출품"} <= patterns
    assert all("제외" in clause["evidence"] or "출품" in clause["evidence"] for clause in clauses)


def test_detect_clauses_returns_empty_for_plain_text():
    assert reuse.detect_clauses("참가 자격: 대학생. 시상내역: 대상 300만원") == []


def test_detect_clauses_finds_duplicate_apply_wording():
    """실측 문구: '중복 신청 또는 중복 참가 지원은 불가함'."""
    clauses = reuse.detect_clauses("중복 신청 또는 중복 참가 지원은 불가함(모든 종목이 동시에 시작함)")
    patterns = {clause["pattern"] for clause in clauses}
    assert "중복 신청" in patterns


def test_check_clause_only(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    report = reuse.check(conn, CLAUSE_TEXT)
    assert report["verdict"] == "clause_only"
    assert report["clauses"] and report["similar_ideas"] == []


def test_check_clear_without_clause_or_ideas(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    report = reuse.check(conn, "참가 자격: 대학생")
    assert report["verdict"] == "clear"


def test_check_warns_when_clause_and_similar_idea(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    db.add_idea(conn, "아차맵 블랙박스 위험구간 지도", base=BASE)
    report = reuse.check(conn, CLAUSE_TEXT, idea_label="아차맵 — 블랙박스 위험구간 지도 서비스")
    assert report["verdict"] == "warn"
    assert report["similar_ideas"][0]["score"] >= 0.5


def test_similar_idea_alone_does_not_flag_clause(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    db.add_idea(conn, "아차맵 블랙박스 위험구간 지도", base=BASE)
    report = reuse.check(conn, "참가 자격: 대학생", idea_label="아차맵 블랙박스 위험구간 지도")
    assert report["verdict"] == "similar_only"


def test_idea_registration_accumulates_contest_ids(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.add_idea(conn, "함께 운동 시간표", "kspo:1", base=BASE)
    saved = db.add_idea(conn, "함께 운동 시간표", "other:2", base=BASE)
    assert saved["contest_ids"] == ["kspo:1", "other:2"]
    assert len(db.ideas(conn)) == 1


def test_dissimilar_idea_is_not_matched(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.add_idea(conn, "부산 해양문학 청소년 글쓰기", base=BASE)
    assert db.similar_ideas(conn, "아차맵 블랙박스 위험구간 지도") == []


def test_short_idea_label_matches_longer_description(tmp_path):
    """짧게 등록한 아이디어도 두 토큰 이상 겹치면 잡아야 한다(리뷰에서 잡은 누락)."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.add_idea(conn, "아차맵 위험구간", base=BASE)
    matches = db.similar_ideas(conn, "아차맵 블랙박스 위험구간 지도 서비스")
    assert matches and matches[0]["score"] >= 0.5


def test_single_token_idea_stays_conservative(tmp_path):
    """토큰 1개짜리 이름은 Jaccard만 쓴다 — 한 단어 겹침으로 경고하지 않는다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.add_idea(conn, "아차맵", base=BASE)
    assert db.similar_ideas(conn, "아차맵 블랙박스 위험구간 지도 서비스") == []
