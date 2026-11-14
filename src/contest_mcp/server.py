"""MCP 서버 (stdio). 툴 12개 + ICS 생성 옵션.

    contest-mcp            # stdio 서버 실행
    python -m contest_mcp.cli --help   # 서버 없이 CLI로 같은 기능

DB 경로: ``CONTEST_MCP_DB`` 환경변수 또는 ``~/.contest-mcp/db.sqlite``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Literal

from . import attachments, db, detail, eligibility, ics, profile, reuse, util
from . import digest as digest_report
from .http import Fetcher
from .sources import SOURCES

DEFAULT_DB = "~/.contest-mcp/db.sqlite"


def db_path() -> Path:
    return Path(os.environ.get("CONTEST_MCP_DB", DEFAULT_DB)).expanduser()


def _connect():
    return db.connect(db_path())


_SHARED_FETCHER: Fetcher | None = None


def _fetcher() -> Fetcher:
    """툴 호출 사이에도 호스트별 요청 간격과 robots 캐시가 유지되도록 하나를 돌려 쓴다."""
    global _SHARED_FETCHER
    if _SHARED_FETCHER is None:
        _SHARED_FETCHER = Fetcher()
    return _SHARED_FETCHER


def refresh_sources(
    sources: list[str] | None = None,
    *,
    max_pages: int = 5,
    base=None,
    fetcher: Fetcher | None = None,
) -> dict:
    """소스들을 수집해 DB에 반영. 소스별 결과와 경고를 함께 돌려준다."""
    ref = base or util.today()
    fetcher = fetcher or _fetcher()
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
        report["warnings"] = list(fetcher.warnings)
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
        status: Literal["open", "upcoming", "all"] = "open",
        limit: int = 20,
        sort: Literal["dday", "views_low", "views_high", "name"] = "dday",
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
            db.flag_submitted(conn, items)
            return {"count": len(items), "sort": sort, "items": items}
        finally:
            conn.close()

    @mcp.tool()
    def get_contest(ident: str, refresh_detail: bool = True, include_attachments: bool = False) -> dict:
        """공모전 1건의 상세(자격·시상·배점·제출물·발표·문의처)를 원문 근거와 함께 가져온다.

        Args:
            ident: `wevity:112073` 같은 id, 상세 URL, 또는 ix·contest_pk 값.
            refresh_detail: True면 상세 페이지를 지금 받아 갱신(하루 1회 캐시).
            include_attachments: True면 첨부(hwpx·docx·pdf)에서 텍스트를 뽑아 **비어 있는 항목만** 채운다
                (최대 2건, 시간이 더 걸린다).
        """
        conn = _connect()
        try:
            item = db.get(conn, ident)
            if item is None:
                return {"found": False, "ident": ident, "hint": "먼저 refresh로 목록을 수집하세요."}
            cached = item.get("detail")
            today = util.today().isoformat()
            refetched = False
            if refresh_detail and (cached is None or item.get("detail_checked_at") != today):
                try:
                    parsed = detail.fetch_and_parse(_fetcher(), item["url"])
                    if not include_attachments:
                        # 첨부를 다시 받지 않는 호출이면, 전에 첨부에서 얻은 내용을 이어 붙인다.
                        detail.carry_attachment_text(parsed, cached)
                    db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
                    item["detail"] = parsed
                    item["detail_checked_at"] = today
                    refetched = True
                except Exception as exc:
                    item["detail_error"] = f"{type(exc).__name__}: {exc}"
            if include_attachments and item.get("detail"):
                has_text = bool(item["detail"].get("attachment_text"))
                already = has_text and not refetched and item.get("detail_checked_at") == today
                if already:
                    item["attachment_cached"] = True
                else:
                    fetcher = _fetcher()
                    results = []
                    texts: list[str] = []
                    for link in (item["detail"].get("attachments") or [])[:2]:
                        parsed_link = attachments.text_of(fetcher, link)
                        results.append(
                            {key: parsed_link[key] for key in ("name", "ext", "method", "chars", "error")}
                        )
                        if parsed_link["text"]:
                            texts.append(parsed_link["text"])
                            detail.enrich_with_text(item["detail"], parsed_link["text"])
                    item["attachment_results"] = results
                    if texts:
                        item["detail"]["attachment_text"] = "\n".join(texts)[:8000]
                    else:
                        # 이번에 첨부를 못 받았으면 전에 받아 둔 내용이라도 유지한다.
                        detail.carry_attachment_text(item["detail"], cached)
                    db.save_detail(
                        conn,
                        item["id"],
                        item["detail"],
                        apply_end=item["detail"].get("deadline_from_detail"),
                    )
            return {"found": True, **item}
        finally:
            conn.close()

    @mcp.tool()
    async def refresh(sources: list[str] | None = None, max_pages: int = 5) -> dict:
        """소스에서 목록을 다시 수집해 DB를 갱신한다.

        소스 5개를 예절 간격(호스트별 1초)을 두고 받느라 30초 이상 걸릴 수 있다.
        수집은 별도 스레드에서 돌아, 그동안 서버가 다른 요청에 응답하지 못하는 일은 없다.

        Args:
            sources: 수집할 소스 이름 목록 (wevity, thinkgood, allcon, linkareer, contestkorea). 생략하면 전체.
            max_pages: 소스별 최대 페이지 수.
        """
        return await asyncio.to_thread(refresh_sources, sources, max_pages=max_pages)

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
            items = db.search(conn, dday_max=limit_days, status="open", limit=200, include_undated=False)
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
                try:
                    result["ics"] = str(ics.write(events, ics_path))
                    result["ics_events"] = len(events)
                except ics.UnsafePath as exc:
                    result["ics_error"] = str(exc)
            return result
        finally:
            conn.close()

    @mcp.tool()
    def mark(
        ident: str,
        state: Literal["submitted", "dropped", "skip"],
        note: str = "",
        submitted_at: str | None = None,
        channel: Literal["email", "form", "site", "other"] | None = None,
        proof: Literal["yes", "no", "unknown"] | None = None,
        proof_path: str | None = None,
        result: Literal["pending", "announced", "won", "lost"] | None = None,
        result_note: str | None = None,
    ) -> dict:
        """공모전에 내 상태와 제출 이력을 기록한다.

        Args:
            ident: contest id, URL, 또는 목록에서 받은 값.
            state: submitted(냈음) | dropped(버림) | skip(관심 없음).
            note: 메모.
            submitted_at: 제출일 (YYYY-MM-DD).
            channel: email | form | site | other.
            proof: 상장·증빙 발급 여부 yes | no | unknown.
            proof_path: 증빙 파일 경로.
            result: pending | announced | won | lost.
            result_note: 결과 메모.
        """
        if state not in {"submitted", "dropped", "skip"}:
            return {"ok": False, "error": "state는 submitted | dropped | skip 중 하나"}
        conn = _connect()
        try:
            item = db.get(conn, ident)
            if item is None:
                return {"ok": False, "error": f"공고를 찾을 수 없음: {ident}"}
            db.set_state(
                conn,
                item["id"],
                state,
                note,
                submitted_at=submitted_at,
                channel=channel,
                proof=proof,
                proof_path=proof_path,
                result=result,
                result_note=result_note,
            )
            saved = next((row for row in db.my_list(conn, state) if row["contest_id"] == item["id"]), {})
            return {"ok": True, "id": item["id"], "name": item["name"], **saved}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    @mcp.tool()
    def my_list(
        state: Literal["submitted", "dropped", "skip"] | None = None,
        missing_proof: bool = False,
    ) -> dict:
        """내가 표시한 공고 목록 (제출/버림/관심 없음).

        Args:
            state: submitted | dropped | skip. 생략하면 전체.
            missing_proof: True면 **제출했는데 증빙(상장 등)이 확인되지 않은 건만**.
        """
        conn = _connect()
        try:
            rows = db.my_list(conn, state, missing_proof=missing_proof)
            return {"count": len(rows), "items": rows}
        finally:
            conn.close()

    @mcp.tool()
    def add_idea(label: str, contest_id: str | None = None, note: str = "") -> dict:
        """내가 낸 아이디어(작품)를 등록한다 — 나중에 재출품 위험을 감지하기 위해.

        Args:
            label: 아이디어/작품 이름 (예: "아차맵 — 블랙박스 위험구간 지도").
            contest_id: 이 아이디어를 낸 공고 id(있으면).
            note: 메모.
        """
        conn = _connect()
        try:
            return {"ok": True, "idea": db.add_idea(conn, label, contest_id, note)}
        finally:
            conn.close()

    @mcp.tool()
    def ideas() -> dict:
        """등록된 내 아이디어 목록."""
        conn = _connect()
        try:
            rows = db.ideas(conn)
            return {"count": len(rows), "items": rows}
        finally:
            conn.close()

    @mcp.tool()
    def check_reuse(ident: str, idea_label: str | None = None) -> dict:
        """재출품 제한 조항과 내 아이디어 유사도를 확인한다(실격 예방).

        Args:
            ident: contest id 또는 URL.
            idea_label: 지금 내려는 아이디어 이름. 주면 등록된 아이디어와 유사도를 비교한다.
        """
        conn = _connect()
        try:
            item = db.get(conn, ident)
            if item is None:
                return {"found": False, "ident": ident}
            old = item.get("detail")
            # 본문 조항(reuse_clauses)이 없는 캐시는 이전 버전이 만든 것 → 본문을 보려면 다시 받아야 한다.
            if not reuse.body_scanned(old):
                try:
                    parsed = detail.fetch_and_parse(_fetcher(), item["url"])
                    if old and old.get("attachment_text"):
                        parsed["attachment_text"] = old["attachment_text"]
                    db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
                    item["detail"] = parsed
                except Exception as exc:
                    if not old:
                        return {"found": True, "id": item["id"], "error": f"상세 파싱 실패: {exc}"}
            detail_data = item.get("detail") or {}
            report = reuse.check(
                conn,
                reuse.text_for(detail_data),
                idea_label=idea_label,
                body_scanned=reuse.body_scanned(detail_data),
            )
            return {"found": True, "id": item["id"], "name": item["name"], "url": item["url"], **report}
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
                    parsed = detail.fetch_and_parse(_fetcher(), item["url"])
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
