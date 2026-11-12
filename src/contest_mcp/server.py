"""MCP 서버 (stdio). 툴 5개 + ICS 생성 옵션.

    contest-mcp            # stdio 서버 실행
    python -m contest_mcp.cli --help   # 서버 없이 CLI로 같은 기능

DB 경로: ``CONTEST_MCP_DB`` 환경변수 또는 ``~/.contest-mcp/db.sqlite``.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from . import db, detail, eligibility, ics, profile, util
from . import digest as digest_report
from .http import DEFAULT_INSECURE_HOSTS, Fetcher
from .sources import SOURCES

DEFAULT_DB = "~/.contest-mcp/db.sqlite"


def db_path() -> Path:
    return Path(os.environ.get("CONTEST_MCP_DB", DEFAULT_DB)).expanduser()


def _connect():
    return db.connect(db_path())


def refresh_sources(
    sources: list[str] | None = None,
    *,
    max_pages: int = 5,
    base=None,
    fetcher: Fetcher | None = None,
) -> dict:
    """소스들을 수집해 DB에 반영. 소스별 결과와 경고를 함께 돌려준다."""
    ref = base or util.today()
    fetcher = fetcher or Fetcher(insecure_hosts=DEFAULT_INSECURE_HOSTS)
    names = sources or list(SOURCES)
    conn = _connect()
    report: dict = {"date": ref.isoformat(), "sources": {}, "warnings": []}
    try:
        for name in names:
            module = SOURCES.get(name)
            if module is None:
                report["sources"][name] = {"error": f"알 수 없는 소스: {name}"}
                continue
            try:
                records = module.fetch(fetcher, max_pages=max_pages, base=ref)
            except Exception as exc:  # 소스 하나가 죽어도 나머지는 계속
                report["sources"][name] = {"error": f"{type(exc).__name__}: {exc}"}
                db.record_run(conn, name, fetched=0, kept=0, error=str(exc), base=ref)
                continue
            stats = db.upsert_many(conn, records, ref)
            report["sources"][name] = {
                "fetched": len(records),
                "new": stats["new"],
                "updated": stats["updated"],
            }
            db.record_run(conn, name, fetched=len(records), kept=len(records), base=ref)
        report["warnings"] = fetcher.warnings
        totals = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE apply_end >= ?) AS open FROM contest",
            (ref.isoformat(),),
        ).fetchone()
        report["total"] = totals["n"]
        report["open"] = totals["open"]
    finally:
        conn.close()
    return report


def _server_factory():
    """mcp 1.x는 FastMCP, 2.x는 MCPServer — 둘 다 지원한다."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ModuleNotFoundError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - 환경 의존
        raise SystemExit("mcp 패키지가 필요합니다: `uv pip install mcp`") from exc


def build_server():
    """MCP 서버 객체."""
    server_cls = _server_factory()
    mcp = server_cls("contest-mcp")

    @mcp.tool()
    def search_contests(
        query: str | None = None,
        field: str | None = None,
        region: str | None = None,
        dday_max: int = 45,
        status: str = "open",
        limit: int = 20,
        sort: str = "dday",
    ) -> dict:
        """공모전을 검색한다 (같은 공고는 여러 소스에 있어도 1건으로 묶인다).

        Args:
            query: 제목·주최 부분 일치 검색어.
            field: 분야 부분 일치 (예: "웹/모바일", "아이디어").
            region: 지역 부분 일치.
            dday_max: 마감까지 남은 일수 상한. 45면 6주 안에 마감하는 것만.
            status: open(진행·예정) | upcoming(접수 전) | all(마감 포함).
            limit: 최대 반환 건수.
            sort: dday(마감 임박) | views_low(조회 적은 순 = 경쟁 약함) | views_high | name.
        """
        conn = _connect()
        try:
            items = db.search(
                conn,
                query=query,
                field=field,
                region=region,
                dday_max=dday_max,
                status=status,
                limit=limit,
                sort=sort,
            )
            submitted = db.submitted_ids(conn)
            for item in items:
                item["already_submitted"] = item["id"] in submitted
            return {"count": len(items), "sort": sort, "items": items}
        finally:
            conn.close()

    @mcp.tool()
    def get_contest(ident: str, refresh_detail: bool = True) -> dict:
        """공모전 1건의 상세(자격·시상·배점·제출물·발표·문의처)를 원문 근거와 함께 가져온다.

        Args:
            ident: `wevity:112073` 같은 id, 상세 URL, 또는 ix·contest_pk 값.
            refresh_detail: True면 상세 페이지를 지금 받아 갱신(하루 1회 캐시).
        """
        conn = _connect()
        try:
            item = db.get(conn, ident)
            if item is None:
                return {"found": False, "ident": ident, "hint": "먼저 refresh로 목록을 수집하세요."}
            cached = item.get("detail")
            today = util.today().isoformat()
            if refresh_detail and (cached is None or item.get("detail_checked_at") != today):
                try:
                    parsed = detail.fetch_and_parse(Fetcher(), item["url"])
                    db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
                    item["detail"] = parsed
                    item["detail_checked_at"] = today
                except Exception as exc:
                    item["detail_error"] = f"{type(exc).__name__}: {exc}"
            return {"found": True, **item}
        finally:
            conn.close()

    @mcp.tool()
    def refresh(sources: list[str] | None = None, max_pages: int = 5) -> dict:
        """소스에서 목록을 다시 수집해 DB를 갱신한다.

        Args:
            sources: 수집할 소스 이름 목록 (wevity, thinkgood, allcon). 생략하면 전체.
            max_pages: 소스별 최대 페이지 수.
        """
        return refresh_sources(sources, max_pages=max_pages)

    @mcp.tool()
    def deadlines(
        days: int = 30,
        ics_path: str | None = None,
        dday_max: int | None = None,
        include_announcements: bool = True,
    ) -> dict:
        """마감 일정을 모은다. `ics_path` 를 주면 ICS 파일도 만든다.

        `include_announcements` 는 상세 원문에서 찾은 **발표일(추정)** 을 함께 넣는다.
        원문 문장에서 뽑은 값이라 ICS 제목에 `[발표?]` 로 표시하고, 근거 문장을 설명에 붙인다.

        Args:
            days: 앞으로 며칠 안의 마감만 (기본 30).
            ics_path: ICS를 저장할 경로 (예: ~/Downloads/contest.ics).
            dday_max: days 대신 남은 일수 상한을 직접 지정.
            include_announcements: 상세에서 찾은 발표일(추정)도 캘린더에 넣을지.
        """
        conn = _connect()
        try:
            limit_days = dday_max if dday_max is not None else days
            items = db.search(conn, dday_max=limit_days, status="open", limit=200)
            events = [
                {
                    "date": item["apply_end"],
                    "summary": f"[마감] {item['name']}",
                    "description": f"{item['host']} · D-{item['dday']} · {'/'.join(item['sources'])}",
                    "url": item["url"],
                }
                for item in items
                if item["apply_end"]
            ]
            today = util.today()
            horizon = (today + dt.timedelta(days=limit_days)).isoformat()
            announcements = []
            if include_announcements:
                announcements = [
                    event
                    for event in db.detail_dates(conn, kinds=("announce",))
                    if today.isoformat() <= event["date"] <= horizon
                ]
                events += [
                    {
                        "date": event["date"],
                        "summary": f"[발표?] {event['name']}",
                        "description": f"{event['host'] or ''} · 원문 언급: {event['evidence'] or ''}".strip(),
                        "url": event["url"],
                        "uid": f"announce-{event['contest_id']}-{event['date']}",
                    }
                    for event in announcements
                ]
            result = {
                "days": limit_days,
                "count": len(items),
                "items": items,
                "announcements": announcements,
                "announcement_note": "발표일은 공고 원문 문장에서 뽑은 추정값입니다. ICS에는 [발표?]로 표시됩니다."
                if include_announcements
                else None,
            }
            if ics_path and events:
                result["ics"] = str(ics.write(events, ics_path))
                result["ics_events"] = len(events)
            return result
        finally:
            conn.close()

    @mcp.tool()
    def mark(ident: str, state: str, note: str = "") -> dict:
        """공모전에 내 상태를 기록한다 (submitted | dropped | skip).

        Args:
            ident: contest id, URL, 또는 목록에서 받은 값.
            state: submitted(냈음) | dropped(버림) | skip(관심 없음).
            note: 메모 (제출 시각·경로 등).
        """
        if state not in {"submitted", "dropped", "skip"}:
            return {"ok": False, "error": "state는 submitted | dropped | skip 중 하나"}
        conn = _connect()
        try:
            item = db.get(conn, ident)
            if item is None:
                return {"ok": False, "error": f"공고를 찾을 수 없음: {ident}"}
            db.set_state(conn, item["id"], state, note)
            return {"ok": True, "id": item["id"], "name": item["name"], "state": state, "note": note}
        finally:
            conn.close()

    @mcp.tool()
    def my_list(state: str | None = None) -> dict:
        """내가 표시한 공고 목록 (제출/버림/관심 없음).

        Args:
            state: submitted | dropped | skip. 생략하면 전체.
        """
        conn = _connect()
        try:
            rows = db.my_list(conn, state)
            return {"count": len(rows), "items": rows}
        finally:
            conn.close()

    @mcp.tool()
    def check_eligibility(ident: str) -> dict:
        """공고의 응모 자격을 프로필(`~/.contest-mcp/profile.json`)과 대조해 축별로 판정한다.

        판정은 pass/fail/unknown 세 가지이며, **원문 문장 근거가 있을 때만** pass/fail을 준다.
        학교·지역(주민등록지 기준)·나이·신분·팀 구성 5축을 본다. 프로필이 없으면 unknown이 정상이다.

        Args:
            ident: contest id, 상세 URL, 또는 ix·contest_pk.
        """
        conn = _connect()
        try:
            item = db.get(conn, ident)
            if item is None:
                return {"found": False, "ident": ident, "hint": "먼저 refresh로 목록을 수집하세요."}
            if not item.get("detail"):
                try:
                    parsed = detail.fetch_and_parse(Fetcher(), item["url"])
                    db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
                    item["detail"] = parsed
                except Exception as exc:
                    return {"found": True, "id": item["id"], "name": item["name"], "error": f"상세 파싱 실패: {exc}"}
            detail_data = item.get("detail") or {}
            fields = detail_data.get("fields") or {}
            parts = [fields[key]["evidence"] for key in ("eligibility", "award", "submission") if key in fields]
            parts.append(detail_data.get("summary") or "")
            text = " ".join(part for part in parts if part)
            user_profile = profile.load()
            result = eligibility.check(text, user_profile)
            return {
                "found": True,
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "apply_end": item["apply_end"],
                "profile_fields_used": sorted(key for key in user_profile if key != "note"),
                "text_chars": len(text),
                **result,
            }
        finally:
            conn.close()

    @mcp.tool()
    def changes(days: int = 7) -> dict:
        """최근 감지된 공고 변경(마감 연장·시상 변경·제목 변경)을 보여준다.

        `refresh`가 매일 돌면서 이전 값과 달라진 항목만 기록한다.

        Args:
            days: 며칠 전까지의 변경을 볼지 (기본 7).
        """
        conn = _connect()
        try:
            rows = db.changes(conn, days=days)
            return {"days": days, "count": len(rows), "items": rows}
        finally:
            conn.close()

    @mcp.tool()
    def digest(days: int = 7, limit: int = 10) -> dict:
        """아침 브리핑: D-N 이내 후보를 **경쟁 약한 순(조회 적은 순)** 으로 정리한다.

        각 항목에 조회수 등급, 원문 제한 문구, 최근 변경, (프로필이 있으면) 자격 판정을 붙인다.
        프로필이 없어도 동작하며, 그때는 판정 대신 제한 문구 유무만 표시한다.

        Args:
            days: 마감까지 남은 일수 기준 (기본 7).
            limit: 최대 건수.
        """
        conn = _connect()
        try:
            return digest_report.build(conn, days=days, limit=limit)
        finally:
            conn.close()

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
