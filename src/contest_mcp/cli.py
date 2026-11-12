"""MCP 없이 쓰는 CLI (서버 디버깅·cron·수동 점검용)."""

from __future__ import annotations

import argparse
import json
import sys

from . import db, detail, ics
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
        flag = " [제출함]" if item["id"] in submitted else ""
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
        items = db.search(conn, dday_max=args.days, status="open", limit=200)
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
        path = ics.write(events, args.ics)
        print(f"ICS 저장: {path} (이벤트 {len(events)}건)")
        return 0
    for item in items:
        print(f"D-{item['dday']:>3} {item['apply_end']} | {item['name'][:60]}")
    print(f"\n{args.days}일 내 마감 {len(items)}건")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        item = db.get(conn, args.ident)
        if item is None:
            print(f"공고를 찾을 수 없음: {args.ident}", file=sys.stderr)
            return 1
        db.set_state(conn, item["id"], args.state, args.note or "")
    finally:
        conn.close()
    print(f"{item['name']} → {args.state}")
    return 0


def cmd_my_list(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        rows = db.my_list(conn, args.state)
    finally:
        conn.close()
    if args.json:
        _print_json(rows)
        return 0
    for row in rows:
        print(f"[{row['state']}] {row['name'] or row['contest_id']} ({row['updated_at']}) {row['note'] or ''}")
    print(f"\n총 {len(rows)}건")
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
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_search)

    detail_cmd = sub.add_parser("detail", help="상세 조회 (원문 파싱)")
    detail_cmd.add_argument("ident")
    detail_cmd.set_defaults(func=cmd_detail)

    deadlines = sub.add_parser("deadlines", help="마감 일정 / ICS 생성")
    deadlines.add_argument("--days", type=int, default=30)
    deadlines.add_argument("--ics", help="ICS 저장 경로")
    deadlines.set_defaults(func=cmd_deadlines)

    mark = sub.add_parser("mark", help="내 상태 기록")
    mark.add_argument("ident")
    mark.add_argument("state", choices=["submitted", "dropped", "skip"])
    mark.add_argument("--note")
    mark.set_defaults(func=cmd_mark)

    mine = sub.add_parser("my-list", help="내가 표시한 공고")
    mine.add_argument("--state", choices=["submitted", "dropped", "skip"])
    mine.set_defaults(func=cmd_my_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "json", False) and args.command == "detail":
        pass
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
