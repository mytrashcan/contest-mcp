from __future__ import annotations

import datetime as dt

from contest_mcp import db

BASE = dt.date(2026, 11, 12)


def _rec(source, key, name, end, basis="list", **extra):
    return {
        "source": source,
        "key": key,
        "name": name,
        "host": extra.get("host", "주최"),
        "url": extra.get("url", f"https://example.com/{source}/{key}"),
        "apply_start": extra.get("apply_start"),
        "apply_end": end,
        "apply_end_basis": basis,
        "status_raw": extra.get("status_raw", "접수중"),
        "views": extra.get("views"),
        "field": extra.get("field"),
        "eligibility": extra.get("eligibility"),
        "region": extra.get("region"),
        "prize": extra.get("prize"),
    }


def test_upsert_counts_new_then_update(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    stats1 = db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], BASE)
    stats2 = db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-21")], BASE)
    assert (stats1["new"], stats1["updated"]) == (1, 0)
    assert (stats2["new"], stats2["updated"]) == (0, 1)
    row = conn.execute("SELECT apply_end, first_seen FROM contest").fetchone()
    assert row["apply_end"] == "2026-11-21"
    assert row["first_seen"] == BASE.isoformat()


def test_detail_date_is_not_overwritten_by_list_date(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20", basis="detail")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-25", basis="badge")], BASE)
    row = conn.execute("SELECT apply_end, apply_end_basis FROM contest").fetchone()
    assert (row["apply_end"], row["apply_end_basis"]) == ("2026-11-20", "detail")


def test_search_groups_same_contest_across_sources(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "[제3회] 매경 AI 광고 공모전", "2026-11-20"),
            _rec("thinkgood", "9", "매경 AI 광고 공모전 (2026)", "2026-11-22"),
            _rec("allcon", "5", "다른 공모전", "2026-12-01"),
        ],
        BASE,
    )
    items = db.search(conn, dday_max=30, base=BASE)
    assert len(items) == 2
    merged = next(item for item in items if "매경" in item["name"])
    assert merged["sources"] == ["thinkgood", "wevity"]
    assert merged["apply_end"] == "2026-11-20"  # 가장 이른 마감을 대표값으로
    assert merged["dday"] == 8
    assert len(merged["ids"]) == 2


def test_search_filters_by_query_field_and_status(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "AI 광고 공모전", "2026-11-20", field="웹/모바일/IT"),
            _rec("wevity", "2", "문학 공모전", "2026-10-01", field="문학/학술"),
            _rec("wevity", "3", "미래 공모전", "2026-12-20", apply_start="2026-12-01"),
        ],
        BASE,
    )
    assert [i["name"] for i in db.search(conn, query="AI", base=BASE)] == ["AI 광고 공모전"]
    assert [i["name"] for i in db.search(conn, field="문학", status="all", base=BASE)] == ["문학 공모전"]
    assert [i["name"] for i in db.search(conn, status="upcoming", base=BASE)] == ["미래 공모전"]
    assert len(db.search(conn, status="all", base=BASE)) == 3


def test_dday_max_excludes_far_deadlines(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "가까운 공모전", "2026-11-15"),
            _rec("wevity", "2", "먼 공모전", "2027-03-01"),
        ],
        BASE,
    )
    assert [i["name"] for i in db.search(conn, dday_max=10, base=BASE)] == ["가까운 공모전"]


def test_save_detail_promotes_deadline_and_get_by_url(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-30")], BASE)
    item = db.get(conn, "https://example.com/wevity/1", BASE)
    assert item is not None
    db.save_detail(conn, item["id"], {"summary": "요약"}, apply_end="2026-11-18", base=BASE)
    refreshed = db.get(conn, "wevity:1", BASE)
    assert refreshed["apply_end"] == "2026-11-18"
    assert refreshed["detail"]["summary"] == "요약"
    assert refreshed["detail_cached"] is True


def test_mark_and_my_list(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20")], BASE)
    db.set_state(conn, "wevity:1", "submitted", "11/12 메일 발송", BASE)
    rows = db.my_list(conn, "submitted")
    assert rows[0]["contest_id"] == "wevity:1"
    assert rows[0]["name"] == "A 공모전"
    assert db.submitted_ids(conn) == {"wevity:1"}


def test_record_run_upserts(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.record_run(conn, "wevity", fetched=10, kept=8, base=BASE)
    db.record_run(conn, "wevity", fetched=12, kept=9, base=BASE)
    rows = db.runs(conn)
    assert len(rows) == 1
    assert (rows[0]["fetched"], rows[0]["kept"]) == (12, 9)


def test_merge_by_token_containment_joins_host_prefixed_title(tmp_path):
    """실측 사례: 같은 공고가 소스마다 주최명이 붙어 다른 이름으로 들어온다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("thinkgood", "1", "제6회 진료정보교류 사업 우수사례 공모전", "2026-11-16"),
            _rec("allcon", "2", "한국보건의료정보원 2026년 제6회 진료정보교류 사업 우수사례 공모전", "2026-11-16"),
        ],
        BASE,
    )
    items = db.search(conn, dday_max=30, base=BASE)
    assert len(items) == 1
    assert sorted(items[0]["sources"]) == ["allcon", "thinkgood"]
    assert len(items[0]["ids"]) == 2


def test_short_names_are_not_merged(tmp_path):
    """토큰 3개 미만이면 포함관계여도 묶지 않는다(오병합 방지)."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "AI 공모전", "2026-11-20"),
            _rec("allcon", "2", "AI 공모전 시상식 안내", "2026-11-20"),
        ],
        BASE,
    )
    assert len(db.search(conn, dday_max=30, base=BASE)) == 2


def test_unrelated_long_names_stay_separate(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "2026 부산 해양문학 공모전 청소년부", "2026-11-20"),
            _rec("thinkgood", "2", "2026 부산 해양문학 공모전 대학부", "2026-11-21"),
        ],
        BASE,
    )
    # 토큰 집합이 서로를 포함하지 않으므로(부문이 다름) 별개로 남는다
    assert len(db.search(conn, dday_max=30, base=BASE)) == 2


def test_get_reads_detail_cache_for_non_representative_id(tmp_path):
    """상세는 그룹 대표 id에 저장된다 — 다른 소스의 id로 물어도 그 캐시를 읽어야 한다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "A 공모전", "2026-11-20", basis="badge"),
            _rec("thinkgood", "9", "A 공모전", "2026-11-21"),
        ],
        BASE,
    )
    representative = db.get(conn, "wevity:1", BASE)
    assert representative["id"] == "thinkgood:9"
    db.save_detail(conn, representative["id"], {"summary": "요약"}, base=BASE)
    again = db.get(conn, "wevity:1", BASE)
    assert again["detail"]["summary"] == "요약"
    assert again["detail_checked_at"] == BASE.isoformat()


def test_undated_contest_drops_out_when_no_longer_listed(tmp_path):
    """마감일이 없는 공고는 날짜로 닫을 수 없으니, 목록에서 사라진 지 오래면 진행 중으로 치지 않는다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "날짜 없는 공모전", None)], BASE)
    assert [item["name"] for item in db.search(conn, base=BASE)] == ["날짜 없는 공모전"]
    assert db.search(conn, base=BASE, include_undated=False) == []
    later = BASE + dt.timedelta(days=db.STALE_UNDATED_DAYS + 1)
    assert db.search(conn, base=later) == []
    assert len(db.search(conn, status="all", base=later)) == 1


def test_detail_date_wins_over_earlier_source_date(tmp_path):
    """상세 원문에서 확인한 날짜가 있으면, 다른 소스의 더 이른 날짜보다 우선한다."""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", "A 공모전", "2026-11-19", basis="badge"),
            _rec("thinkgood", "9", "A 공모전", "2026-11-21"),
        ],
        BASE,
    )
    db.save_detail(conn, "wevity:1", {"summary": ""}, apply_end="2026-11-25", base=BASE)
    item = db.search(conn, dday_max=30, base=BASE)[0]
    assert item["id"] == "wevity:1"
    assert item["apply_end"] == "2026-11-25"
    assert item["dday"] == 13
    assert item["apply_end_by_source"] == {"wevity": "2026-11-25", "thinkgood": "2026-11-21"}


def test_missing_list_date_does_not_erase_known_deadline(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", "2026-11-20", apply_start="2026-11-01")], BASE)
    db.upsert_many(conn, [_rec("wevity", "1", "A 공모전", None)], BASE)
    row = conn.execute("SELECT apply_start, apply_end, apply_end_basis FROM contest").fetchone()
    assert tuple(row) == ("2026-11-01", "2026-11-20", "list")
    assert db.changes(conn, days=7, base=BASE) == []


def test_get_groups_the_same_way_search_does(tmp_path):
    """이름 해시가 달라도 토큰 포함관계로 묶인 공고는 get에서도 같은 묶음이어야 한다."""
    conn = db.connect(tmp_path / "t.sqlite")
    long_name = "한국보건의료정보원 2026년 제6회 진료정보교류 서비스 아이디어 공모전"
    short_name = "제6회 진료정보교류 서비스 아이디어 공모전"
    db.upsert_many(
        conn,
        [
            _rec("wevity", "1", long_name, "2026-11-20"),
            _rec("thinkgood", "9", short_name, "2026-11-20"),
            _rec("allcon", "3", "제5회 진료정보교류 서비스 아이디어 공모전", "2025-11-20"),
        ],
        BASE,
    )
    searched = db.search(conn, dday_max=30, base=BASE)
    assert len(searched) == 1
    expected = sorted(searched[0]["ids"])
    assert expected == ["thinkgood:9", "wevity:1"]
    for ident in ("wevity:1", "thinkgood:9"):
        assert sorted(db.get(conn, ident, BASE)["ids"]) == expected
    # 마감된 지난 회차는 올해 공고와 합치지 않는다
    assert db.get(conn, "allcon:3", BASE)["ids"] == ["allcon:3"]
