"""범용 감시(watch) 검증 — 레지스트리·URL diff·공모전 일정·검색 감시. 네트워크는 쓰지 않는다."""

from __future__ import annotations

import datetime as dt
import json

from contest_mcp import db, watch
from contest_mcp.http import Fetcher

BASE = dt.date(2026, 11, 12)

PAGE = """
<html><body>
<div>2026 대국민 아이디어 공모전 참가 안내</div>
<div>2026 대국민 아이디어 공모전 수상자 발표 (2026.12.05)</div>
<div>2026 아이디어 공모전 발표심사 안내</div>
</body></html>
"""

PAGE_NEW = PAGE.replace("</body>", "<div>최종 수상작 발표 — 대상 1팀 (2026.12.12)</div></body>")


class FakeFetcher(Fetcher):
    """URL -> HTML 고정 응답. 실패를 흉내내려면 페이지를 넣지 않는다."""

    def __init__(self, pages: dict[str, str]):
        super().__init__()
        self.pages = pages

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        if url not in self.pages:
            raise RuntimeError(f"페이지 없음: {url}")
        return self.pages[url]


def _blank() -> dict:
    return {"version": 2, "targets": [], "last_run": None, "last_failures": []}


def _save(tmp_path, *targets: tuple[watch.Kind, str], **kwargs):
    state = _blank()
    for kind, value in targets:
        watch.add_target(state, kind, value, **kwargs)
    path = tmp_path / "watch.json"
    watch.save_state(state, path)
    return path


# ------------------------------------------------------------------ 파싱
def test_lines_of_splits_blocks_and_drops_scripts():
    html = "<script>var x=1</script><div>짧음</div><p>2026 공모전 결과 발표 안내</p>"
    assert watch.lines_of(html) == ["2026 공모전 결과 발표 안내"]


def test_url_hits_use_default_result_words():
    hits = watch.url_hits(PAGE, None)
    assert any("수상자 발표" in line for line in hits)
    # '발표심사'는 결과 소식이 아니다(기본 매칭에 '발표'를 넣지 않은 이유)
    assert not any("발표심사" in line for line in hits)
    assert hits[next(iter(hits))]["date"]


def test_url_hits_respect_custom_match():
    hits = watch.url_hits(PAGE, r"참가 안내")
    assert list(hits) == ["2026 대국민 아이디어 공모전 참가 안내"]


# ------------------------------------------------------------------ 레지스트리
def test_add_target_is_idempotent_and_updatable():
    state = _blank()
    watch.add_target(state, "url", "https://example.com/n", match="수상")
    watch.add_target(state, "url", "https://example.com/n", match="결과", label="주최 공지")
    assert len(state["targets"]) == 1
    assert state["targets"][0]["match"] == "결과"
    assert state["targets"][0]["label"] == "주최 공지"


def test_remove_target_by_id_and_index():
    state = _blank()
    watch.add_target(state, "query", "AI")
    watch.add_target(state, "url", "https://example.com/a")
    assert watch.remove_target(state, "1") is True
    assert [t["id"] for t in state["targets"]] == ["url:https://example.com/a"]
    assert watch.remove_target(state, "url:https://example.com/a") is True
    assert watch.remove_target(state, "없는값") is False


def test_list_targets_shows_counts():
    state = _blank()
    watch.add_target(state, "contest", "wevity:1", label="내 공고")
    rows = watch.list_targets(state)
    assert rows[0]["index"] == 1
    assert rows[0]["kind"] == "contest"
    assert rows[0]["keys"] == 0


# ------------------------------------------------------------------ 점검
def test_no_targets_returns_note(tmp_path):
    report = watch.check(fetcher=FakeFetcher({}), state_path=tmp_path / "watch.json")
    assert report["new"] == []
    assert report["checked"] == 0
    assert "등록된 감시 대상이 없습니다" in report["note"]


def test_url_baseline_then_new_then_deduped(tmp_path):
    url = "https://example.com/notice"
    path = _save(tmp_path, ("url", url))
    first = watch.check(fetcher=FakeFetcher({url: PAGE}), state_path=path)
    assert first["new"] == []
    assert first["baselined"] == [f"url:{url}"]

    second = watch.check(fetcher=FakeFetcher({url: PAGE_NEW}), state_path=path)
    assert [item["title"] for item in second["new"]] == ["최종 수상작 발표 — 대상 1팀 (2026.12.12)"]
    assert second["new"][0]["kind"] == "url"

    third = watch.check(fetcher=FakeFetcher({url: PAGE_NEW}), state_path=path)
    assert third["new"] == []


def test_baseline_flag_silences_existing_changes(tmp_path):
    url = "https://example.com/notice"
    path = _save(tmp_path, ("url", url))
    watch.check(fetcher=FakeFetcher({url: PAGE}), state_path=path)
    changed = FakeFetcher({url: PAGE_NEW})
    assert len(watch.check(fetcher=changed, state_path=path)["new"]) == 1
    reset = watch.check(fetcher=changed, state_path=path, baseline=True)
    assert reset["new"] == []
    assert reset["baselined"] == [f"url:{url}"]


def test_contest_target_reports_schedule_change(tmp_path, monkeypatch):
    path = _save(tmp_path, ("contest", "contestkorea:1"))
    mentions = {"심사 2026-11-02": {"title": "심사 2026-11-02", "url": "https://example.com/1"}}

    def fake_detail(fetcher, conn, ident):
        return dict(mentions), "https://example.com/1"

    monkeypatch.setattr(watch, "detail_hits", fake_detail)
    assert watch.check(fetcher=FakeFetcher({}), state_path=path)["new"] == []

    mentions["발표 2026-12-05"] = {"title": "발표 2026-12-05", "url": "https://example.com/1"}
    fresh = watch.check(fetcher=FakeFetcher({}), state_path=path)["new"]
    assert [item["title"] for item in fresh] == ["발표 2026-12-05"]
    assert fresh[0]["kind"] == "contest"


def test_query_target_alerts_on_new_contest(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")

    def rec(key, name):
        return {
            "source": "wevity",
            "key": key,
            "name": name,
            "host": "주최",
            "url": f"https://example.com/{key}",
            "apply_start": None,
            "apply_end": "2026-12-20",
            "apply_end_basis": "list",
            "status_raw": "접수중",
            "views": 10,
            "field": None,
            "eligibility": None,
            "region": None,
            "prize": None,
        }

    db.upsert_many(conn, [rec("1", "AI 공모전 A")], BASE)
    path = _save(tmp_path, ("query", "AI"))
    assert watch.check(fetcher=FakeFetcher({}), conn=conn, state_path=path)["new"] == []

    db.upsert_many(conn, [rec("2", "AI 공모전 B"), rec("3", "요리 공모전")], BASE)
    fresh = watch.check(fetcher=FakeFetcher({}), conn=conn, state_path=path)["new"]
    assert [item["title"] for item in fresh] == ["AI 공모전 B"]
    assert fresh[0]["kind"] == "query"
    conn.close()


def test_all_targets_failing_is_flagged(tmp_path):
    path = _save(tmp_path, ("url", "https://example.com/broken"))
    report = watch.check(fetcher=FakeFetcher({}), state_path=path)
    assert report["failures"]
    assert report["all_failed"] is True


def test_state_survives_broken_json(tmp_path):
    path = tmp_path / "watch.json"
    path.write_text("{not json", encoding="utf-8")
    assert watch.load_state(path)["targets"] == []


def test_detail_hits_drop_site_chrome_mentions(monkeypatch):
    """공고 페이지에 섞인 다른 공모전 배너 일정은 감시 대상이 아니다."""
    from contest_mcp import detail

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
    assert list(mentions) == ["심사 2026-11-02"]
    assert url == "https://example.com/view/1"


def test_saved_state_is_json_readable(tmp_path):
    path = _save(tmp_path, ("query", "AI"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["targets"][0]["id"] == "query:AI"
