"""SQLite 저장소: 공고 upsert, 그룹 중복 제거 조회, 내 제출 상태."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from . import util

SCHEMA = """
CREATE TABLE IF NOT EXISTS contest (
    id              TEXT PRIMARY KEY,   -- source:key
    source          TEXT NOT NULL,
    key             TEXT NOT NULL,
    group_id        TEXT NOT NULL,      -- 이름 정규화 해시 (소스 간 중복 묶기)
    name            TEXT NOT NULL,
    host            TEXT,
    url             TEXT,
    field           TEXT,
    apply_start     TEXT,
    apply_end       TEXT,
    apply_end_basis TEXT,               -- badge | list | detail
    status_raw      TEXT,
    views           INTEGER,
    eligibility     TEXT,
    region          TEXT,
    prize           TEXT,
    detail_json     TEXT,
    detail_at       TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);
CREATE INDEX IF NOT EXISTS idx_contest_group ON contest(group_id);
CREATE INDEX IF NOT EXISTS idx_contest_end ON contest(apply_end);
CREATE TABLE IF NOT EXISTS my_list (
    contest_id TEXT PRIMARY KEY,
    state      TEXT NOT NULL,           -- submitted | dropped | skip
    note       TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_run (
    source    TEXT PRIMARY KEY,
    last_run  TEXT,
    fetched   INTEGER,
    kept      INTEGER,
    error     TEXT
);
"""

BASIS_RANK = {"badge": 0, "list": 1, "detail": 2}
MIN_MERGE_TOKENS = 3


def _mergeable(left: set[str], right: set[str]) -> bool:
    """한쪽 이름의 의미 토큰이 다른 쪽에 통째로 들어 있으면 같은 공고로 본다.

    토큰이 3개 미만인 짧은 이름은 오병합 위험이 커서 묶지 않는다.
    """
    if not left or not right:
        return False
    small, big = (left, right) if len(left) <= len(right) else (right, left)
    return len(small) >= MIN_MERGE_TOKENS and small <= big


def connect(path: str | Path) -> sqlite3.Connection:
    """DB 연결 (없으면 스키마 생성)."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row(record: dict, base: dt.date) -> dict:
    name = record["name"]
    return {
        "id": f"{record['source']}:{record['key']}",
        "source": record["source"],
        "key": str(record["key"]),
        "group_id": util.group_id(name),
        "name": name,
        "host": record.get("host") or "",
        "url": record.get("url") or "",
        "field": record.get("field"),
        "apply_start": record.get("apply_start"),
        "apply_end": record.get("apply_end"),
        "apply_end_basis": record.get("apply_end_basis") or "list",
        "status_raw": record.get("status_raw"),
        "views": record.get("views"),
        "eligibility": record.get("eligibility"),
        "region": record.get("region"),
        "prize": record.get("prize"),
        "first_seen": base.isoformat(),
        "last_seen": base.isoformat(),
    }


def upsert_many(conn: sqlite3.Connection, records: list[dict], base: dt.date | None = None) -> dict:
    """레코드를 저장. 상세에서 얻은 마감일(basis=detail)은 목록 날짜로 덮어쓰지 않는다."""
    ref = base or util.today()
    stats = {"new": 0, "updated": 0}
    for record in records:
        if not record.get("name"):
            continue
        fresh = _row(record, ref)
        existing = conn.execute(
            "SELECT apply_end, apply_end_basis FROM contest WHERE id = ?", (fresh["id"],)
        ).fetchone()
        if existing is None:
            columns = ", ".join(fresh)
            placeholders = ", ".join("?" * len(fresh))
            conn.execute(f"INSERT INTO contest ({columns}) VALUES ({placeholders})", tuple(fresh.values()))
            stats["new"] += 1
            continue

        old_rank = BASIS_RANK.get(existing["apply_end_basis"] or "list", 0)
        new_rank = BASIS_RANK.get(fresh["apply_end_basis"], 0)
        if existing["apply_end"] and old_rank > new_rank:
            fresh["apply_end"] = existing["apply_end"]
            fresh["apply_end_basis"] = existing["apply_end_basis"]
        assignments = ", ".join(f"{column} = ?" for column in fresh if column not in {"id", "first_seen"})
        values = [value for column, value in fresh.items() if column not in {"id", "first_seen"}]
        conn.execute(
            f"UPDATE contest SET {assignments} WHERE id = ?",
            (*values, fresh["id"]),
        )
        stats["updated"] += 1
    conn.commit()
    return stats


def _group(rows: list[sqlite3.Row], base: dt.date) -> list[dict]:
    """같은 공고를 하나로 묶는다: (1) 이름 정규화 해시 동일, (2) 토큰 포함관계.

    (2)는 주최명이 제목 앞에 붙어 이름이 달라진 경우를 잡는다
    (예: ``한국보건의료정보원 2026년 제6회 진료정보교류 …`` ⊃ ``제6회 진료정보교류 …``).
    짧은 이름이 우연히 포함되는 오병합을 막으려고 토큰 3개 이상일 때만 인정한다.
    """
    parent = {row["id"]: row["id"] for row in rows}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_group: dict[str, list[str]] = {}
    for row in rows:
        if row["group_id"]:
            by_group.setdefault(row["group_id"], []).append(row["id"])
    for ids in by_group.values():
        for other in ids[1:]:
            union(ids[0], other)

    tokens = {row["id"]: util.name_tokens(row["name"]) for row in rows}
    index: dict[str, list[str]] = {}
    for rid, token_set in tokens.items():
        for token in token_set:
            index.setdefault(token, []).append(rid)
    for rid, token_set in tokens.items():
        candidates: set[str] = set()
        for token in token_set:
            candidates.update(index.get(token, ()))
        candidates.discard(rid)
        for other in candidates:
            if _mergeable(token_set, tokens[other]):
                union(rid, other)

    buckets: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        buckets.setdefault(find(row["id"]), []).append(row)

    grouped: list[dict] = []
    for group_rows in buckets.values():
        best = sorted(
            group_rows,
            key=lambda r: (
                -BASIS_RANK.get(r["apply_end_basis"] or "list", 0),
                r["apply_end"] or "9999-12-31",
            ),
        )[0]
        sources = sorted({r["source"] for r in group_rows})
        ends = sorted({r["apply_end"] for r in group_rows if r["apply_end"]})
        detail = json.loads(best["detail_json"]) if best["detail_json"] else None
        grouped.append(
            {
                "id": best["id"],
                "ids": [r["id"] for r in group_rows],
                "name": best["name"],
                "host": best["host"],
                "url": best["url"],
                "field": best["field"],
                "region": best["region"],
                "apply_start": best["apply_start"],
                "apply_end": ends[0] if ends else best["apply_end"],
                "apply_end_by_source": {
                    r["source"]: r["apply_end"] for r in group_rows if r["apply_end"]
                },
                "dday": util.dday_of(ends[0] if ends else best["apply_end"], base),
                "status": util.status_of(best["apply_start"], ends[0] if ends else best["apply_end"], base),
                "sources": sources,
                "views": max((r["views"] or 0) for r in group_rows) or None,
                "eligibility": best["eligibility"],
                "prize": best["prize"],
                "detail_cached": detail is not None,
                "detail_checked_at": best["detail_at"],
            }
        )
    return grouped


def search(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    field: str | None = None,
    region: str | None = None,
    dday_max: int | None = 45,
    status: str = "open",
    limit: int = 20,
    base: dt.date | None = None,
) -> list[dict]:
    """그룹 중복을 제거한 검색. ``status='all'`` 이면 마감 포함."""
    ref = base or util.today()
    clauses: list[str] = []
    params: list[object] = []
    if query:
        clauses.append("(name LIKE ? OR host LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    if field:
        clauses.append("field LIKE ?")
        params.append(f"%{field}%")
    if region:
        clauses.append("region LIKE ?")
        params.append(f"%{region}%")
    if status == "open":
        clauses.append("(apply_end IS NULL OR apply_end >= ?)")
        params.append(ref.isoformat())
    elif status == "upcoming":
        clauses.append("apply_start > ?")
        params.append(ref.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM contest {where}", params).fetchall()
    grouped = _group(rows, ref)
    for item in grouped:
        if item["status"] == "closed":
            item["status"] = util.status_of(item["apply_start"], item["apply_end"], ref)
    if status == "open":
        grouped = [item for item in grouped if item["status"] in {"open", "unknown"}]
    elif status == "upcoming":
        grouped = [item for item in grouped if item["status"] == "upcoming"]
    if dday_max is not None:
        grouped = [item for item in grouped if item["dday"] is None or item["dday"] <= dday_max]
    grouped.sort(key=lambda item: (item["dday"] is None, item["dday"] or 10**6))
    return grouped[:limit]


def get(conn: sqlite3.Connection, ident: str, base: dt.date | None = None) -> dict | None:
    """id·url·ix·contest_pk 어느 형태로도 조회."""
    ident = (ident or "").strip()
    row = conn.execute("SELECT * FROM contest WHERE id = ?", (ident,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM contest WHERE url = ?", (ident,)).fetchone()
    if row is None:
        tail = ident.rstrip("/").rsplit("=", 1)[-1]
        row = conn.execute(
            "SELECT * FROM contest WHERE key = ? OR url LIKE ? LIMIT 1", (tail, f"%{tail}%")
        ).fetchone()
    if row is None:
        return None
    siblings = conn.execute("SELECT * FROM contest WHERE group_id = ?", (row["group_id"],)).fetchall()
    item = _group(siblings, base or util.today())[0]
    item["detail"] = json.loads(row["detail_json"]) if row["detail_json"] else None
    return item


def save_detail(
    conn: sqlite3.Connection,
    contest_id: str,
    detail: dict,
    *,
    apply_end: str | None = None,
    base: dt.date | None = None,
) -> None:
    """상세 파싱 결과를 저장. 원문에서 얻은 마감일은 basis=detail로 승격한다."""
    ref = (base or util.today()).isoformat()
    conn.execute(
        "UPDATE contest SET detail_json = ?, detail_at = ? WHERE id = ?",
        (json.dumps(detail, ensure_ascii=False), ref, contest_id),
    )
    if apply_end:
        conn.execute(
            "UPDATE contest SET apply_end = ?, apply_end_basis = 'detail' WHERE id = ?",
            (apply_end, contest_id),
        )
    conn.commit()


def set_state(
    conn: sqlite3.Connection,
    contest_id: str,
    state: str,
    note: str = "",
    base: dt.date | None = None,
) -> None:
    conn.execute(
        "INSERT INTO my_list (contest_id, state, note, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(contest_id) DO UPDATE SET "
        "state = excluded.state, note = excluded.note, updated_at = excluded.updated_at",
        (contest_id, state, note, (base or util.today()).isoformat()),
    )
    conn.commit()


def my_list(conn: sqlite3.Connection, state: str | None = None) -> list[dict]:
    sql = (
        "SELECT m.contest_id, m.state, m.note, m.updated_at, c.name, c.host, c.url, c.apply_end "
        "FROM my_list m LEFT JOIN contest c ON c.id = m.contest_id"
    )
    params: tuple[object, ...] = ()
    if state:
        sql += " WHERE m.state = ?"
        params = (state,)
    sql += " ORDER BY m.updated_at DESC"
    return [dict(row) for row in conn.execute(sql, params)]


def submitted_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT contest_id FROM my_list WHERE state = 'submitted'")}


def record_run(
    conn: sqlite3.Connection,
    source: str,
    *,
    fetched: int,
    kept: int,
    error: str | None = None,
    base: dt.date | None = None,
) -> None:
    conn.execute(
        "INSERT INTO source_run (source, last_run, fetched, kept, error) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(source) DO UPDATE SET last_run = excluded.last_run, fetched = excluded.fetched, "
        "kept = excluded.kept, error = excluded.error",
        (source, (base or util.today()).isoformat(), fetched, kept, error),
    )
    conn.commit()


def runs(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM source_run ORDER BY source")]
