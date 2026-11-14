"""MCP 없이 쓰는 CLI (서버 디버깅·cron·수동 점검용)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import attachments, db, detail, eligibility, ics, profile, reuse, util
from . import digest as digest_report
from .http import Fetcher
from .server import db_path, refresh_sources


def _open():
    return db.connect(db_path())


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_refresh(args: argparse.Namespace) -> int:
    sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    report = refresh_sources(sources, max_pages=args.pages)
    if args.json:
        _print_json(report)
        return 0
    print(f"[{report['date']}] 전체 {report['total']}건 / 마감 전 {report['open']}건")
    for name, result in report["sources"].items():
        if "error" in result:
            print(f"  - {name}: 실패 {result['error']}")
        else:
            print(f"  - {name}: {result['fetched']}건 (신규 {result['new']}, 갱신 {result['updated']})")
    for warning in report.get("warnings", []):
        print(f"  ! {warning}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        items = db.search(
            conn,
            query=args.query,
            field=args.field,
            region=args.region,
            dday_max=args.dday,
            status=args.status,
            limit=args.limit,
            sort=args.sort,
        )
        submitted = db.submitted_ids(conn)
    finally:
        conn.close()
    if args.json:
        _print_json(items)
        return 0
    if not items:
        print("조건에 맞는 공고가 없습니다.")
        return 0
    for item in items:
        dday = f"D-{item['dday']}" if item["dday"] is not None else "D-?"
        flag = " [제출함]" if submitted.intersection(item["ids"]) else ""
        views = f" 조회 {item['views']:,}" if item.get("views") else ""
        print(f"{dday:>6} {item['apply_end'] or '날짜없음'} | {item['name'][:60]}")
        print(f"       {item['host'] or '-'} · {'/'.join(item['sources'])}{views}{flag}")
        print(f"       {item['url']}")
    print(f"\n총 {len(items)}건")
    return 0


def cmd_detail(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        item = db.get(conn, args.ident)
        if item is None:
            print(f"공고를 찾을 수 없음: {args.ident}", file=sys.stderr)
            return 1
        parsed = detail.fetch_and_parse(Fetcher(), item["url"])
        if args.attachments and parsed.get("attachments"):
            fetcher = Fetcher()
            results = []
            texts: list[str] = []
            for link in parsed["attachments"][:2]:
                got = attachments.text_of(fetcher, link)
                results.append(got)
                if got["text"]:
                    texts.append(got["text"])
                    detail.enrich_with_text(parsed, got["text"])
            parsed["attachment_results"] = [
                {key: got[key] for key in ("name", "ext", "method", "chars", "error")} for got in results
            ]
            if texts:
                # 재출품 조항·유의사항은 첨부에만 있는 경우가 많아 앞부분을 보관한다(8천 자 상한).
                parsed["attachment_text"] = "\n".join(texts)[:8000]
        db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
    finally:
        conn.close()
    if args.json:
        _print_json({**item, "detail": parsed})
        return 0
    print(f"{item['name']}\n{item['host'] or '-'} · {item['url']}")
    print(f"마감 {item['apply_end'] or '미상'} (D-{item['dday']}) · 출처 {'/'.join(item['sources'])}")
    if parsed.get("summary"):
        print(f"\n요약: {parsed['summary']}")
    print(f"\n확인된 항목 ({len(parsed['fields'])}):")
    for key, info in parsed["fields"].items():
        print(f"  [{key}] {info['evidence'][:150]}")
    if parsed["missing"]:
        print(f"\n원문에서 못 찾은 항목: {', '.join(parsed['missing'])}")
    if parsed.get("date_mentions"):
        print("\n발표·심사 관련 날짜 언급:")
        for mention in parsed["date_mentions"][:5]:
            print(f"  {mention['date']} {mention['label']} — {mention['evidence'][:90]}")
    return 0


def cmd_deadlines(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        items = db.search(conn, dday_max=args.days, status="open", limit=200, include_undated=False)
        announcements = []
        if not args.no_announce:
            today = util.today()
            horizon = (today + dt.timedelta(days=args.days)).isoformat()
            announcements = [
                event
                for event in db.detail_dates(conn, kinds=("announce",))
                if today.isoformat() <= event["date"] <= horizon
            ]
    finally:
        conn.close()
    if args.ics:
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
        path = ics.write(events, args.ics)
        dated = len(items) - sum(1 for item in items if not item["apply_end"])
        print(f"ICS 저장: {path} (마감 {dated}건 + 발표 추정 {len(announcements)}건)")
        return 0
    for item in items:
        print(f"D-{item['dday']:>3} {item['apply_end']} | {item['name'][:60]}")
    if announcements:
        print("\n발표일(원문 추정):")
        for event in announcements:
            print(f"  {event['date']} {event['name'][:50]} — {(event['evidence'] or '')[:60]}")
    print(f"\n{args.days}일 내 마감 {len(items)}건 / 발표 추정 {len(announcements)}건")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        report = digest_report.build(conn, days=args.days, limit=args.limit, use_profile=not args.no_profile)
    finally:
        conn.close()
    if args.json:
        _print_json(report)
        return 0
    print(report["text"])
    return 0


def cmd_changes(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        rows = db.changes(conn, days=args.days)
    finally:
        conn.close()
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print(f"최근 {args.days}일 변경 없음")
        return 0
    labels = {"apply_end": "마감", "apply_start": "접수시작", "prize": "시상", "name": "제목"}
    for row in rows:
        print(f"[{row['seen_at']}] {labels.get(row['field'], row['field'])}: "
              f"{row['old_value']} → {row['new_value']}")
        print(f"    {row['name'] or row['contest_id']}  {row['url'] or ''}")
    print(f"\n총 {len(rows)}건")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        item = db.get(conn, args.ident)
        if item is None:
            print(f"공고를 찾을 수 없음: {args.ident}", file=sys.stderr)
            return 1
        if not item.get("detail"):
            parsed = detail.fetch_and_parse(Fetcher(), item["url"])
            db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
            item["detail"] = parsed
    finally:
        conn.close()
    detail_data = item.get("detail") or {}
    fields = detail_data.get("fields") or {}
    parts = [fields[key]["evidence"] for key in ("eligibility", "award", "submission") if key in fields]
    parts.append(detail_data.get("summary") or "")
    text = " ".join(part for part in parts if part)
    user_profile = profile.load()
    result = eligibility.check(text, user_profile)
    if args.json:
        _print_json({**result, "profile": user_profile, "name": item["name"]})
        return 0
    print(f"{item['name']}\n{item['url']}")
    if not user_profile:
        print("! 프로필 없음 → ~/.contest-mcp/profile.json 을 만들면 판정이 채워집니다")
    print(f"\n종합: {result['overall']}")
    for axis in result["axes"]:
        mark = {"pass": "O", "fail": "X", "unknown": "?"}[axis["verdict"]]
        print(f"  [{mark}] {axis['axis']}: {axis['reason']}")
        if axis["evidence"]:
            print(f"      근거: {axis['evidence'][:120]}")
    print(f"\n{result['note']}")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        item = db.get(conn, args.ident)
        if item is None:
            print(f"공고를 찾을 수 없음: {args.ident}", file=sys.stderr)
            return 1
        db.set_state(
            conn,
            item["id"],
            args.state,
            args.note or "",
            submitted_at=args.submitted_at,
            channel=args.channel,
            proof=args.proof,
            proof_path=args.proof_path,
            result=args.result,
            result_note=args.result_note,
        )
        row = next((r for r in db.my_list(conn, args.state) if r["contest_id"] == item["id"]), {})
    finally:
        conn.close()
    print(f"{item['name']} → {args.state}")
    if args.json:
        _print_json(row)
    return 0


def cmd_my_list(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        rows = db.my_list(conn, args.state, missing_proof=args.missing_proof)
    finally:
        conn.close()
    if args.json:
        _print_json(rows)
        return 0
    labels = {"submitted": "제출", "dropped": "폐기", "skip": "관심없음"}
    for row in rows:
        proof = {"yes": "증빙O", "no": "증빙X"}.get(row["proof"] or "", "증빙?")
        result = row["result"] or "-"
        print(f"[{labels.get(row['state'], row['state'])}] {row['name'] or row['contest_id']}")
        print(f"    제출 {row['submitted_at'] or '-'} · 경로 {row['channel'] or '-'} · {proof} · 결과 {result}"
              f"{' · ' + row['note'] if row['note'] else ''}")
    print(f"\n총 {len(rows)}건")
    return 0


def cmd_idea(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        if args.action == "add":
            idea = db.add_idea(conn, args.label, args.contest_id, args.note or "")
            print(f"등록: {idea['label']} (id {idea['id']}, 공고 {idea['contest_ids']})")
        else:
            rows = db.ideas(conn)
            for row in rows:
                print(f"[{row['id']}] {row['label']} — 공고 {row['contest_ids']} {row['note'] or ''}")
            print(f"\n총 {len(rows)}건")
    finally:
        conn.close()
    return 0


def cmd_reuse(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        item = db.get(conn, args.ident)
        if item is None:
            print(f"공고를 찾을 수 없음: {args.ident}", file=sys.stderr)
            return 1
        old = item.get("detail")
        if not reuse.body_scanned(old):  # 이전 버전 캐시는 본문 조항이 없다 → 다시 받는다
            try:
                parsed = detail.fetch_and_parse(Fetcher(), item["url"])
            except Exception:
                if not old:
                    raise
            else:
                if old and old.get("attachment_text"):
                    parsed["attachment_text"] = old["attachment_text"]
                db.save_detail(conn, item["id"], parsed, apply_end=parsed.get("deadline_from_detail"))
                item["detail"] = parsed
        detail_data = item.get("detail") or {}
        report = reuse.check(
            conn,
            reuse.text_for(detail_data),
            idea_label=args.idea,
            body_scanned=reuse.body_scanned(detail_data),
        )
    finally:
        conn.close()
    if args.json:
        _print_json(report)
        return 0
    print(f"{item['name']}")
    print(f"판정: {report['verdict']} — {report['reason']}")
    for clause in report["clauses"]:
        print(f"  조항 [{clause['pattern']}]: {clause['evidence'][:110]}")
    for idea in report["similar_ideas"]:
        print(f"  유사 아이디어 {idea['score']}: {idea['label']} (공고 {idea['contest_ids']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contest", description="contest-mcp CLI")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser("refresh", help="소스에서 목록 수집")
    refresh.add_argument("--sources", help="쉼표 구분 (wevity,thinkgood,allcon)")
    refresh.add_argument("--pages", type=int, default=5, help="소스별 최대 페이지")
    refresh.set_defaults(func=cmd_refresh)

    search = sub.add_parser("search", help="공고 검색")
    search.add_argument("--query")
    search.add_argument("--field")
    search.add_argument("--region")
    search.add_argument("--dday", type=int, default=45)
    search.add_argument("--status", default="open", choices=["open", "upcoming", "all"])
    search.add_argument("--sort", default="dday", choices=["dday", "views_low", "views_high", "name"])
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_search)

    detail_cmd = sub.add_parser("detail", help="상세 조회 (원문 파싱)")
    detail_cmd.add_argument("ident")
    detail_cmd.add_argument("--attachments", action="store_true", help="첨부(hwpx·pdf)에서 빈 항목 채우기")
    detail_cmd.set_defaults(func=cmd_detail)

    deadlines = sub.add_parser("deadlines", help="마감 일정 / ICS 생성")
    deadlines.add_argument("--days", type=int, default=30)
    deadlines.add_argument("--ics", help="ICS 저장 경로")
    deadlines.add_argument("--no-announce", action="store_true", help="발표일(추정) 제외")
    deadlines.set_defaults(func=cmd_deadlines)

    check = sub.add_parser("check", help="자격 판정 (프로필 대조)")
    check.add_argument("ident")
    check.set_defaults(func=cmd_check)

    dig = sub.add_parser("digest", help="아침 브리핑 (D-N, 경쟁 약한 순)")
    dig.add_argument("--days", type=int, default=7)
    dig.add_argument("--limit", type=int, default=10)
    dig.add_argument("--no-profile", action="store_true", help="프로필 무시(판정 없이)")
    dig.set_defaults(func=cmd_digest)

    chg = sub.add_parser("changes", help="최근 공고 변경(마감 연장·시상 변경)")
    chg.add_argument("--days", type=int, default=7)
    chg.set_defaults(func=cmd_changes)

    mark = sub.add_parser("mark", help="내 상태·제출 이력 기록")
    mark.add_argument("ident")
    mark.add_argument("state", choices=["submitted", "dropped", "skip"])
    mark.add_argument("--note")
    mark.add_argument("--submitted-at", dest="submitted_at", help="제출일 YYYY-MM-DD")
    mark.add_argument("--channel", choices=["email", "form", "site", "other"])
    mark.add_argument("--proof", choices=["yes", "no", "unknown"], help="상장·증빙 발급 여부")
    mark.add_argument("--proof-path", dest="proof_path")
    mark.add_argument("--result", choices=["pending", "announced", "won", "lost"])
    mark.add_argument("--result-note", dest="result_note")
    mark.set_defaults(func=cmd_mark)

    mine = sub.add_parser("my-list", help="내가 표시한 공고")
    mine.add_argument("--state", choices=["submitted", "dropped", "skip"])
    mine.add_argument("--missing-proof", action="store_true", help="증빙 미확인 제출건만")
    mine.set_defaults(func=cmd_my_list)

    idea = sub.add_parser("idea", help="내 아이디어 등록/목록 (재출품 감지용)")
    idea.add_argument("action", choices=["add", "list"])
    idea.add_argument("label", nargs="?", help="아이디어 이름 (add일 때)")
    idea.add_argument("--contest-id", dest="contest_id")
    idea.add_argument("--note")
    idea.set_defaults(func=cmd_idea)

    reusecmd = sub.add_parser("reuse", help="재출품 제한 조항·내 아이디어 유사도 확인")
    reusecmd.add_argument("ident")
    reusecmd.add_argument("--idea", help="지금 내려는 아이디어 이름")
    reusecmd.set_defaults(func=cmd_reuse)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "json", False) and args.command == "detail":
        pass
    try:
        return args.func(args)
    except (db.AmbiguousIdent, ics.UnsafePath) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
