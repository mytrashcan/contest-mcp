"""재출품 금지 조항 탐지 + 내 아이디어 유사도 경고.

공모전 대부분은 "타 공모전 출품작·수상작 재출품 금지" 조항을 둔다. 마감이 임박할수록 기존 작품을
재포장하고 싶어지는데 그게 정확히 실격 축이라, 제출 전에 원문 조항과 내 아이디어 목록을 함께 보여준다.
"""

from __future__ import annotations

import re

from . import db, util

# 조항 탐지 패턴. 하나라도 걸리면 '재출품 제한 있음'으로 보고 근거 문장을 함께 준다.
PATTERNS = (
    "타 공모전",
    "다른 공모전",
    "타 공모",
    "중복 응모",
    "중복응모",
    "중복 신청",
    "중복신청",
    "중복 참가",
    "중복 지원",
    "중복 수상",
    "중복수상",
    "재출품",
    "이미 출품",
    "기출품",
    "동일 작품",
    "유사 작품",
    "표절",
    "모방",
)

_CLAUSE = re.compile("|".join(re.escape(pattern) for pattern in PATTERNS))


def detect_clauses(text: str, width: int = 120) -> list[dict]:
    """조항 문구와 그 주변 문장을 돌려준다(중복 제거)."""
    found: list[dict] = []
    seen: set[str] = set()
    body = util.clean(text or "")
    for match in _CLAUSE.finditer(body):
        start = max(0, match.start() - 40)
        evidence = body[start : match.start() + width].strip()
        key = match.group(0)
        if key in seen:
            continue
        seen.add(key)
        found.append({"pattern": key, "evidence": evidence})
    return found


def body_scanned(detail_data: dict | None) -> bool:
    """상세 파싱이 본문 전체에서 조항을 모아 뒀는지(이전 버전 캐시에는 이 키가 없다)."""
    return "reuse_clauses" in (detail_data or {})


def text_for(detail_data: dict | None) -> str:
    """조항 탐지에 넘길 텍스트: 요약·첨부·필드 근거 + 본문 전체에서 모아 둔 조항 문장."""
    data = detail_data or {}
    parts = [data.get("summary") or "", data.get("attachment_text") or ""]
    parts += [info.get("evidence") or "" for info in (data.get("fields") or {}).values()]
    parts += [clause.get("evidence") or "" for clause in data.get("reuse_clauses") or []]
    return " / ".join(part for part in parts if part)


def check(
    conn,
    text: str,
    *,
    idea_label: str | None = None,
    threshold: float = 0.5,
    body_scanned: bool = True,
) -> dict:
    """재출품 조항 + (선택) 내 아이디어 유사도.

    ``body_scanned=False`` 는 본문 전체를 보지 못했다는 뜻이다(상세 재수집 실패). 그때 'clear'는 믿을 수 없다.
    """
    clauses = detect_clauses(text)
    similar = db.similar_ideas(conn, idea_label, threshold=threshold) if idea_label else []
    if clauses and similar:
        verdict = "warn"
        reason = "재출품 제한 조항이 있고, 내가 낸 아이디어와 유사한 것이 있습니다. 재사용하면 실격 위험이 큽니다."
    elif clauses:
        verdict = "clause_only"
        reason = "재출품 제한 조항이 있습니다. 새 아이디어로 쓰는 것이 안전합니다."
    elif similar:
        verdict = "similar_only"
        reason = "조항은 못 찾았지만 내가 낸 아이디어와 유사합니다. 원문 유의사항을 직접 확인하세요."
    else:
        verdict = "clear"
        reason = "재출품 제한 조항을 찾지 못했고 유사 아이디어도 없습니다(조항이 첨부파일에만 있을 수 있음)."
    if not clauses and not body_scanned:
        reason += " 단, 본문 전체는 확인하지 못했습니다(상세 재수집 실패) — 원문 유의사항을 직접 확인하세요."
    return {
        "verdict": verdict,
        "reason": reason,
        "body_scanned": body_scanned,
        "clauses": clauses,
        "similar_ideas": similar,
        "idea_label": idea_label,
    }
