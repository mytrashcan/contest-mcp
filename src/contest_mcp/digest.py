"""아침 브리핑 텍스트: 마감 임박 + 경쟁 강도 + 자격/변경 배지.

프로필이 없어도 동작한다. 그때는 판정 대신 **원문 제한 문구 유무**만 표시한다.
(예: "제한: 대학생·대학원생" / "제한 문구 미확인")
"""

from __future__ import annotations

import datetime as dt

from . import db, eligibility, profile, util

VIEW_LOW = 300
VIEW_MID = 3000

FIELD_LABEL = {
    "apply_end": "마감",
    "apply_start": "접수시작",
    "prize": "시상",
    "name": "제목",
}


def view_grade(views: int | None) -> str:
    if views is None:
        return "조회 미확인"
    if views <= VIEW_LOW:
        return f"조회 {views:,} (낮음)"
    if views <= VIEW_MID:
        return f"조회 {views:,} (중간)"
    return f"조회 {views:,} (높음)"


CUT_MARKERS = ("시상", "홈페이지", "첨부파일", "첨부", "키워드", "공모분야", "접수기간", "접수 방법")


def _restriction_badge(detail: dict) -> str:
    fields = detail.get("fields") or {}
    if "eligibility" not in fields:
        return "제한 문구 미확인"
    text = util.clean(fields["eligibility"]["evidence"]).split("■")[0]
    for marker in CUT_MARKERS:
        index = text.find(marker)
        if index > 0:
            text = text[:index]
    text = text.strip(" -·,:/")
    return f"제한: {text[:50]}" if text else "제한 문구 미확인"


def build(
    conn,
    *,
    days: int = 7,
    limit: int = 10,
    base: dt.date | None = None,
    use_profile: bool = True,
) -> dict:
    ref = base or util.today()
    items = db.search(conn, dday_max=days, status="open", limit=limit, sort="views_low", base=ref)
    recent_changes = db.changes(conn, days=days, base=ref)
    changes_by_contest: dict[str, list[dict]] = {}
    for change in recent_changes:
        changes_by_contest.setdefault(change["contest_id"], []).append(change)

    user_profile = profile.load() if use_profile else {}
    rows = []
    lines = [f"[contest-mcp] {ref.isoformat()} 기준 D-{days} 이내 {len(items)}건"]
    for index, item in enumerate(items, start=1):
        full = db.get(conn, item["id"], ref) or {}
        detail = full.get("detail") or {}
        badges = [view_grade(item["views"]), _restriction_badge(detail)]

        if user_profile:
            parts = [
                (detail.get("fields") or {}).get(key, {}).get("evidence")
                for key in ("eligibility", "award", "submission")
            ]
            text = " ".join(part for part in parts if part) or detail.get("summary") or ""
            verdict = eligibility.check(text, user_profile)
            # 축을 하나도 확정하지 못했으면 배지를 붙이지 않는다(모두 '?'면 정보가 없다).
            if verdict["overall"] != "unknown":
                mark = "자격 O" if verdict["overall"] == "pass" else "자격 X"
                if verdict["failed_axes"]:
                    mark += f"({', '.join(verdict['failed_axes'])})"
                badges.append(mark)

        changes = changes_by_contest.get(item["id"], [])
        if changes:
            labels = ", ".join(
                f"{FIELD_LABEL.get(change['field'], change['field'])} {change['old_value']}→{change['new_value']}"
                for change in changes[:2]
            )
            badges.append(f"변경: {labels}")

        lines.append(f"{index}. D-{item['dday']} · {item['name']}")
        lines.append(f"   {' · '.join(badges)}")
        rows.append({"id": item["id"], "name": item["name"], "dday": item["dday"], "badges": badges})

    if recent_changes:
        lines.append("")
        lines.append(f"최근 {days}일 변경 {len(recent_changes)}건")
        for change in recent_changes[:5]:
            lines.append(
                f"  - {change['name'] or change['contest_id']}: "
                f"{FIELD_LABEL.get(change['field'], change['field'])} {change['old_value']}→{change['new_value']}"
            )

    return {
        "date": ref.isoformat(),
        "days": days,
        "count": len(items),
        "items": rows,
        "changes": recent_changes,
        "profile_used": bool(user_profile),
        "text": "\n".join(lines),
    }
