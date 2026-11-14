"""리뷰에서 나온 안전장치 검증: ident 모호성·소스 0건·robots·주소 스킴·ICS 경로·압축 크기."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest

from contest_mcp import attachments, db, detail, http, ics, util
from contest_mcp.sources import wevity

BASE = dt.date(2026, 11, 12)


def _rec(source, key, name):
    return {
        "source": source,
        "key": key,
        "name": name,
        "host": "주최",
        "url": f"https://example.com/{source}/view?no={key}",
        "apply_end": "2026-11-20",
        "apply_end_basis": "list",
    }


# ------------------------------------------------------------------ ident
def test_get_refuses_to_guess_between_contests(tmp_path):
    """짧은 조각이나 여러 소스에 겹치는 key로 아무 공고나 집지 않는다(mark가 엉뚱한 곳에 기록됨)."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "112316", "A 공모전"),
            _rec("allcon", "112316", "B 경진대회"),
            _rec("thinkgood", "77", "C 해커톤"),
        ],
        BASE,
    )
    assert db.get(conn, "", BASE) is None
    assert db.get(conn, "1", BASE) is None
    assert db.get(conn, "77", BASE)["id"] == "thinkgood:77"
    assert db.get(conn, "https://other.example/view?contest_pk=77", BASE)["id"] == "thinkgood:77"
    assert db.get(conn, "wevity:112316", BASE)["id"] == "wevity:112316"
    with pytest.raises(db.AmbiguousIdent) as info:
        db.get(conn, "112316", BASE)
    assert set(info.value.ids) == {"wevity:112316", "allcon:112316"}


# ------------------------------------------------------------------ 소스 0건
def test_source_with_no_rows_on_first_page_raises():
    class Blank:
        def get(self, url, headers=None):
            return "<html><body>점검 중</body></html>"

    with pytest.raises(util.EmptyListing):
        wevity.fetch(Blank(), max_pages=3, base=BASE)
    util.ensure_first_page("위비티", 2)  # 2페이지부터는 정상적인 끝


# ------------------------------------------------------------------ robots
ROBOTS = """
User-agent: Googlebot
Disallow: /

User-agent: *
User-agent: other
Disallow: /private/
Allow: /private/open/
Disallow: /*?c=admin
Disallow: /file$
Disallow:
"""


def test_robots_star_group_with_allow_wildcard_and_query():
    rules = http.parse_robots(ROBOTS)
    assert http.robots_verdict(rules, "/")[0] is True
    assert http.robots_verdict(rules, "/private/x")[0] is False
    assert http.robots_verdict(rules, "/private/open/x")[0] is True
    assert http.robots_verdict(rules, "/?c=admin&x=1")[0] is False
    assert http.robots_verdict(rules, "/?c=find")[0] is True
    assert http.robots_verdict(rules, "/file")[0] is False
    assert http.robots_verdict(rules, "/file.txt")[0] is True


def test_robots_prefers_group_naming_this_crawler():
    rules = http.parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: contest-mcp\nDisallow: /admin/\n")
    assert http.robots_verdict(rules, "/list")[0] is True
    allowed, reason = http.robots_verdict(rules, "/admin/x")
    assert not allowed and "/admin/" in reason


class _CannedFetcher(http.Fetcher):
    """네트워크 대신 정해둔 robots.txt와 본문을 돌려준다."""

    def __init__(self, robots):
        super().__init__()
        self.robots = robots
        self.requested: list[str] = []

    def _raw(self, url, *, headers, data=None):
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            if isinstance(self.robots, Exception):
                raise self.robots
            return self.robots.encode()
        return b"ok"


def test_fetcher_checks_query_string_against_robots():
    fetcher = _CannedFetcher("User-agent: *\nDisallow: /*?secret=\n")
    assert fetcher.get("https://example.com/list?page=1") == "ok"
    with pytest.raises(http.RobotsDenied):
        fetcher.get("https://example.com/list?secret=1")


def test_missing_robots_is_silent_but_unreachable_robots_warns():
    missing = _CannedFetcher(http.FetchError("HTTP 404", status=404))
    assert missing.get("https://example.com/a") == "ok"
    assert missing.warnings == []
    broken = _CannedFetcher(http.FetchError("HTTP 503", status=503))
    assert broken.get("https://example.com/a") == "ok"
    assert any("robots.txt 확인 실패" in note for note in broken.warnings)


# ------------------------------------------------------------------ 주소 스킴
def test_non_web_urls_are_refused_before_any_request():
    fetcher = _CannedFetcher("")
    for url in ("file:///etc/passwd", "ftp://example.com/a.pdf", "/relative/a.pdf"):
        with pytest.raises(http.FetchError):
            fetcher.get_bytes(url)
    assert fetcher.requested == []


def test_extract_links_skips_non_web_schemes():
    html = '<a href="file:///C:/secret.pdf">공고문.pdf</a><a href="/files/a.pdf">a.pdf</a>'
    links = attachments.extract_links(html, "https://example.com/view")
    assert [link["url"] for link in links] == ["https://example.com/files/a.pdf"]


# ------------------------------------------------------------------ 압축 크기
def test_oversized_zip_member_is_refused_with_reason(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_MEMBER_BYTES", 10)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:p>" + "가" * 100 + "</w:p>")

    class FakeFetcher:
        def get_bytes(self, url):
            return buffer.getvalue()

    link = {"url": "https://example.com/a.docx", "name": "a.docx", "ext": ".docx"}
    result = attachments.text_of(FakeFetcher(), link)
    assert result["text"] == ""
    assert "크기 초과" in result["error"]


# ------------------------------------------------------------------ ICS 경로
def test_ics_write_refuses_non_ics_targets(tmp_path):
    events = [{"date": "2026-11-20", "summary": "[마감] A"}]
    with pytest.raises(ics.UnsafePath):
        ics.write(events, tmp_path / "notes.txt")
    assert not (tmp_path / "notes.txt").exists()

    taken = tmp_path / "taken.ics"
    taken.write_text("내 메모", encoding="utf-8")
    with pytest.raises(ics.UnsafePath):
        ics.write(events, taken)
    assert taken.read_text(encoding="utf-8") == "내 메모"

    target = tmp_path / "contest.ics"
    ics.write(events, target)
    ics.write(events, target)  # 직접 만든 ICS는 다시 덮어쓸 수 있다
    assert target.read_bytes().startswith(b"BEGIN:VCALENDAR")


# ------------------------------------------------------------------ 내부 주소
def test_internal_hosts_are_refused_before_any_request():
    fetcher = _CannedFetcher("")
    for url in (
        "http://localhost:8000/a.pdf",
        "http://127.0.0.1/a.pdf",
        "http://192.168.0.10/a.pdf",
        "http://[::1]/a.pdf",
        "http://nas.local/a.pdf",
    ):
        with pytest.raises(http.FetchError):
            fetcher.get_bytes(url)
    assert fetcher.requested == []
    assert fetcher.get("https://example.com/a") == "ok"


# ------------------------------------------------------------------ robots 캐시
def test_robots_cache_is_reused_then_expires():
    cached = _CannedFetcher("User-agent: *\nDisallow: /private/\n")
    cached.get("https://example.com/a")
    cached.get("https://example.com/b")
    assert sum(url.endswith("/robots.txt") for url in cached.requested) == 1

    expiring = _CannedFetcher("User-agent: *\nDisallow: /private/\n")
    expiring.robots_ttl = 0
    expiring.get("https://example.com/a")
    expiring.get("https://example.com/b")
    assert sum(url.endswith("/robots.txt") for url in expiring.requested) == 2


# ------------------------------------------------------------------ 첨부 내용 보존
def test_refetched_detail_keeps_attachment_findings():
    previous = {"attachment_text": "심사기준: 창의성 40점, 실현가능성 60점", "fields": {}}
    parsed = {"fields": {}, "missing": ["scoring"]}
    detail.carry_attachment_text(parsed, previous)
    assert parsed["attachment_text"].startswith("심사기준")
    assert parsed["fields"]["scoring"]["source"] == "attachment"
    assert "scoring" not in parsed["missing"]

    fresh = {"attachment_text": "새 첨부", "fields": {}, "missing": []}
    detail.carry_attachment_text(fresh, previous)
    assert fresh["attachment_text"] == "새 첨부"  # 방금 다시 받은 첨부가 있으면 덮어쓰지 않는다
