"""결과 감시(watch) 검증 — 파싱·기준선·중복 방지. 네트워크는 쓰지 않는다."""

from __future__ import annotations

from contest_mcp import watch
from contest_mcp.http import Fetcher

BOARD_HTML = """
<table><tbody>
<tr><td>12</td><td><a href="/v/12">2026 KEIT 대국민 혁신 아이디어 공모전 수상작 발표</a></td><td>2026.12.04</td><td>1234</td></tr>
<tr><td>11</td><td>2026년 사업 설명회 안내</td><td>2026.11.20</td><td>88</td></tr>
<tr><td>10</td><td>2026 대국민 아이디어 공모전 발표심사 안내</td><td>2026.11.18</td><td>51</td></tr>
</tbody></table>
"""

NEW_ROW = (
    '<tr><td>13</td><td><a href="/v/13">2026 대국민 혁신 아이디어 공모전 수상자 발표</a></td>'
    "<td>2026.12.12</td><td>7</td></tr>"
)


class FakeFetcher(Fetcher):
    """URL -> HTML 고정 응답. 실패를 흉내내려면 페이지를 넣지 않는다."""

    def __init__(self, pages: dict[str, str]):
        super().__init__()
        self.pages = pages

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        if url not in self.pages:
            raise RuntimeError(f"페이지 없음: {url}")
        return self.pages[url]


def _source(url: str = "https://example.com/notice") -> watch.BoardSource:
    return watch.BoardSource("테스트 게시판", url, r"아이디어|공모전|수상")


def test_rows_split_by_table_row_and_keep_cells():
    lines = watch.rows(BOARD_HTML)
    assert len(lines) == 3
    assert "2026 KEIT 대국민 혁신 아이디어 공모전 수상작 발표" in lines[0]
    assert watch.date_of(lines[0]) == "2026.12.04"


def test_title_of_skips_number_and_date_cells():
    row = "12 | 2026 KEIT 대국민 혁신 아이디어 공모전 수상작 발표 | 2026.12.04 | 1234"
    assert watch.title_of(row) == "2026 KEIT 대국민 혁신 아이디어 공모전 수상작 발표"
    assert watch.title_of("1 | 2026.12.04 | 7") is None


def test_board_hits_use_result_words_not_evaluation_word():
    hits = watch.board_hits(BOARD_HTML, _source())
    assert "2026 KEIT 대국민 혁신 아이디어 공모전 수상작 발표" in hits
    # '발표심사'는 결과 소식이 아니라 심사 안내다 — 잡지 않는다.
    assert not any("발표심사" in title for title in hits)
    # 결과 단어가 없는 공지는 제외
    assert not any("설명회" in title for title in hits)


def test_board_hits_require_source_keyword():
    source = watch.BoardSource("테스트", "https://example.com/notice", r"규제")
    assert watch.board_hits(BOARD_HTML, source) == {}


def test_first_run_is_baseline_then_new_then_deduped(tmp_path, monkeypatch):
    url = "https://example.com/notice"
    monkeypatch.setattr(watch, "BOARD_SOURCES", (_source(url),))
    monkeypatch.setattr(watch, "DETAIL_SOURCES", {})
    state = tmp_path / "watch.json"

    first = watch.check(fetcher=FakeFetcher({url: BOARD_HTML}), state_path=state)
    assert first["new"] == []
    assert first["baselined"] == ["테스트 게시판"]

    changed = FakeFetcher({url: BOARD_HTML.replace("</tbody>", NEW_ROW + "</tbody>")})
    second = watch.check(fetcher=changed, state_path=state)
    assert [item["title"] for item in second["new"]] == ["2026 대국민 혁신 아이디어 공모전 수상자 발표"]
    assert second["new"][0]["kind"] == "board"
    assert second["new"][0]["date"] == "2026.12.12"

    third = watch.check(fetcher=changed, state_path=state)
    assert third["new"] == []


def test_baseline_flag_silences_and_resets(tmp_path, monkeypatch):
    url = "https://example.com/notice"
    monkeypatch.setattr(watch, "BOARD_SOURCES", (_source(url),))
    monkeypatch.setattr(watch, "DETAIL_SOURCES", {})
    state = tmp_path / "watch.json"

    watch.check(fetcher=FakeFetcher({url: BOARD_HTML}), state_path=state)
    changed = FakeFetcher({url: BOARD_HTML.replace("</tbody>", NEW_ROW + "</tbody>")})
    assert len(watch.check(fetcher=changed, state_path=state)["new"]) == 1

    reset = watch.check(fetcher=changed, state_path=state, baseline=True)
    assert reset["new"] == []
    assert reset["baselined"] == ["테스트 게시판"]


def test_schedule_change_is_reported_as_detail_item(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "BOARD_SOURCES", ())
    monkeypatch.setattr(watch, "DETAIL_SOURCES", {"성평등 AI 챌린지(공고)": "contestkorea:1"})
    state = tmp_path / "watch.json"
    mentions = {"심사 2026-11-02": "심사기간 2026.11.02 ~ 2026.11.30"}

    def fake_detail(fetcher, conn, ident):
        return dict(mentions), "https://example.com/view/1"

    monkeypatch.setattr(watch, "detail_hits", fake_detail)
    first = watch.check(fetcher=FakeFetcher({}), conn=None, state_path=state)
    assert first["new"] == []

    mentions["심사 2026-11-25"] = "심사기간 2026.11.25"
    second = watch.check(fetcher=FakeFetcher({}), conn=None, state_path=state)
    assert [item["title"] for item in second["new"]] == ["심사 2026-11-25"]
    assert second["new"][0]["kind"] == "schedule"
    assert second["new"][0]["url"] == "https://example.com/view/1"


def test_all_sources_failing_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "BOARD_SOURCES", (_source("https://example.com/broken"),))
    monkeypatch.setattr(watch, "DETAIL_SOURCES", {})
    report = watch.check(fetcher=FakeFetcher({}), state_path=tmp_path / "watch.json")
    assert report["failures"]
    assert report["all_failed"] is True


def test_state_survives_broken_json(tmp_path):
    state = tmp_path / "watch.json"
    state.write_text("{not json", encoding="utf-8")
    assert watch.load_state(state) == {"version": 1, "sources": {}}


def test_detail_hits_drop_site_chrome_mentions(monkeypatch):
    """공고 페이지에 섞인 다른 공모전 배너 일정은 감시 대상이 아니다."""
    from contest_mcp import db, detail

    monkeypatch.setattr(db, "get", lambda conn, ident: {"url": "https://example.com/view/1"})
    monkeypatch.setattr(
        detail,
        "fetch_and_parse",
        lambda fetcher, url: {
            "date_mentions": [
                {"label": "발표", "date": "2026-11-27", "evidence": "발표 11/27일로 연기] 문화·관광 D-13 접수중"},
                {"label": "심사", "date": "2026-11-02", "evidence": "심사기간 2026.11.02 ~ 2026.11.30 대회지역 온라인"},
            ]
        },
    )
    mentions, url = watch.detail_hits(FakeFetcher({}), conn=object(), ident="contestkorea:1")
    assert mentions == {"심사 2026-11-02": "심사기간 2026.11.02 ~ 2026.11.30 대회지역 온라인"}
    assert url == "https://example.com/view/1"
