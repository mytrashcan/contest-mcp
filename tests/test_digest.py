"""아침 브리핑(digest) 텍스트·배지 검증."""

from __future__ import annotations

import datetime as dt

from contest_mcp import db, digest

BASE = dt.date(2026, 11, 12)


def _seed(conn) -> None:
    db.upsert_many(
        conn,
        [
            {
                "source": "wevity",
                "key": "1",
                "name": "한산한 공모전",
                "host": "주최A",
                "url": "https://example.com/1",
                "apply_end": "2026-11-15",
                "apply_end_basis": "list",
                "views": 42,
            },
            {
                "source": "wevity",
                "key": "2",
                "name": "인기 공모전",
                "host": "주최B",
                "url": "https://example.com/2",
                "apply_end": "2026-11-14",
                "apply_end_basis": "list",
                "views": 47160,
            },
            {
                "source": "wevity",
                "key": "3",
                "name": "조회 미확인 공모전",
                "host": "주최C",
                "url": "https://example.com/3",
                "apply_end": "2026-11-18",
                "apply_end_basis": "list",
            },
        ],
        BASE,
    )
    db.save_detail(
        conn,
        "wevity:1",
        {"fields": {"eligibility": {"label": "지원 자격", "evidence": "지원 자격 - 전국 대학생"}}, "summary": ""},
        base=BASE,
    )


def test_digest_sorts_by_low_views_and_adds_badges(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    report = digest.build(conn, days=7, limit=5, base=BASE, use_profile=False)
    text = report["text"]
    assert report["count"] == 3
    assert text.index("한산한 공모전") < text.index("인기 공모전")
    assert "조회 42 (낮음)" in text
    assert "조회 47,160 (높음)" in text
    assert "조회 미확인" in text
    assert "제한: 지원 자격 - 전국 대학생" in text
    assert "제한 문구 미확인" in text
    # 프로필을 안 쓰면 판정 배지(자격 O/X/?)는 붙지 않는다
    assert "자격 O" not in text and "자격 X" not in text and "자격 ?" not in text


def test_digest_without_profile_still_works(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    report = digest.build(conn, days=7, base=BASE, use_profile=False)
    assert report["profile_used"] is False
    verdicts = {"자격 O", "자격 X", "자격 ?"}
    assert all(badge not in verdicts for item in report["items"] for badge in item["badges"])


def test_digest_with_profile_adds_verdict_badge(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    # 자격 문구를 지역 제한으로 바꿔 'fail' 축이 생기게 한다
    db.save_detail(
        conn,
        "wevity:1",
        {"fields": {"eligibility": {"label": "지원 자격", "evidence": "지원 자격 - 경북도민 누구나"}}, "summary": ""},
        base=BASE,
    )
    monkeypatch.setattr(
        digest.profile,
        "load",
        lambda: {"school": "경북대학교", "region": "대구", "status": "학부생", "age": 24, "team_available": True},
    )
    report = digest.build(conn, days=7, base=BASE)
    badges = [badge for item in report["items"] for badge in item["badges"]]
    assert any(badge.startswith("자격 X(region") for badge in badges), badges
    assert report["profile_used"] is True


def test_digest_skips_verdict_badge_when_all_axes_unknown(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)  # 자격 문구가 '전국 대학생' → 지역·나이·학교는 판정 불가
    monkeypatch.setattr(digest.profile, "load", lambda: {"school": "경북대학교", "region": "경북"})
    report = digest.build(conn, days=7, base=BASE)
    badges = [badge for item in report["items"] for badge in item["badges"]]
    assert not any(badge.startswith("자격") for badge in badges), badges


def test_digest_reports_recent_changes(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    db.upsert_many(
        conn,
        [
            {
                "source": "wevity",
                "key": "1",
                "name": "한산한 공모전",
                "host": "주최A",
                "url": "https://example.com/1",
                "apply_end": "2026-11-22",
                "apply_end_basis": "list",
                "views": 42,
            }
        ],
        BASE,
    )
    report = digest.build(conn, days=7, base=BASE, use_profile=False)
    assert "최근 7일 변경 1건" in report["text"]
    assert "마감 2026-11-15→2026-11-22" in report["text"]
