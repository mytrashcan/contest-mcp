"""상세 페이지 해석: 자격·시상·배점·제출물·발표·문의처를 '근거 문장'과 함께 뽑는다.

원칙: 없는 항목을 만들어 내지 않는다. 못 찾으면 ``missing`` 에 넣고, 찾은 항목은
원문에서 그대로 잘라낸 문장(``evidence``)을 함께 반환해 사람이 검증할 수 있게 한다.
목록 단계에서 확보한 마감일보다 상세 원문의 접수기간이 우선한다.
"""

from __future__ import annotations

import re

from . import attachments, util

LABELS: dict[str, tuple[str, ...]] = {
    "eligibility": (
        "지원자격",
        "지원 자격",
        "참가자격",
        "참가 자격",
        "참가대상",
        "참가 대상",
        "지원대상",
        "지원 대상",
        "응모자격",
        "응모 자격",
        "모집대상",
        "참여대상",
        "응모대상",
        "자격요건",
        "자격 요건",
    ),
    "award": ("시상내역", "시상 내역", "시상규모", "시상 규모", "포상내역", "상금"),
    "scoring": ("배점", "심사기준", "심사 기준", "평가항목", "평가 항목", "심사항목", "심사 항목"),
    "submission": ("제출서류", "제출 서류", "제출물", "제출형식", "제출 형식", "분량"),
    "presentation": ("발표심사", "발표 심사", "본선", "필참", "비대면", "오프라인"),
    "contact": ("문의처", "문의처:", "문의", "담당자"),
    "prize_people": ("시상인원", "시상 인원", "선정인원", "수상인원"),
}

META = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.S | re.I)
META_ALT = re.compile(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', re.S | re.I)
OG_TITLE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.S | re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
PERIOD = re.compile(r"(?:접수|모집|신청|응모)\s*기간[^0-9]{0,12}(.{0,80})", re.S)
_DATE_CONTEXT = re.compile(
    r"(발표|결과\s*발표|심사|본선|시상식|시상)[^0-9\n]{0,30}?"
    r"((?:20\d{2}\s*[.\-/년]\s*)?\d{1,2}\s*[.\-/월]\s*\d{1,2})"
)
# 발표일(결과 발표)만 캘린더 승격 대상으로 삼는다. 심사·시상은 참고용 라벨.
_KIND = (
    ("발표", "announce"),
    ("심사", "review"),
    ("본선", "review"),
    ("시상", "award"),
)


def _kind_of(label: str) -> str:
    for token, kind in _KIND:
        if token in label:
            return kind
    return "review"


def _find_label(body: str, label: str) -> int | None:
    """라벨 위치를 찾되, 네비게이션 잔재('참가대상 | 주최.주관 | 총상금')는 건너뛴다."""
    start = 0
    while True:
        index = body.find(label, start)
        if index < 0:
            return None
        tail = body[index + len(label) : index + len(label) + 80].lstrip(" :：-·")
        looks_like_nav = not tail or len(tail) < 4 or tail.startswith(("|", "주최", "총상금", "홈페이지"))
        if not looks_like_nav:
            return index
        start = index + len(label)


def parse(html: str, url: str = "", base=None) -> dict:
    """상세 HTML → 구조화된 요약 + 근거 문장."""
    ref = base or util.today()
    text = util.clean(html)
    meta_match = META.search(html) or META_ALT.search(html)
    summary = util.clean(meta_match.group(1)) if meta_match else ""
    title_match = OG_TITLE.search(html) or TITLE.search(html)
    title = util.clean(title_match.group(1)) if title_match else ""

    # 본문에서 라벨을 찾아 뒤따르는 문장을 잘라낸다. 라벨 자체는 텍스트에서만 찾는다.
    # meta 요약이 있으면 요약을 먼저 보고(주최사가 쓴 구조화 문장), 없으면 본문에서 찾는다.
    found: dict[str, dict] = {}
    haystacks = [part for part in (summary, text) if part]
    for key, labels in LABELS.items():
        for label in labels:
            hit = None
            for source_text in haystacks:
                index = _find_label(source_text, label)
                if index is not None:
                    hit = source_text[index : index + 240].strip()
                    break
            if hit:
                found[key] = {"label": label, "evidence": hit}
                break

    deadline = None
    period_match = PERIOD.search(text) or PERIOD.search(summary)
    if period_match:
        _start, end = util.parse_period(period_match.group(1), ref)
        deadline = end

    # 발표·심사·시상은 "문장 속 날짜"로만 존재한다. 추론하지 않고 언급된 날짜와 문맥을 그대로 모은다.
    date_mentions: list[dict] = []
    mention_text = " ".join(haystacks)
    for match in _DATE_CONTEXT.finditer(mention_text):
        parsed = util.parse_date(match.group(2), ref)
        if parsed:
            date_mentions.append(
                {
                    "label": match.group(1),
                    "kind": _kind_of(match.group(1)),
                    "date": parsed,
                    "evidence": mention_text[match.start() : match.start() + 90].strip(),
                }
            )

    missing = [key for key in LABELS if key not in found]
    presented = _presentation(text, summary)
    return {
        "url": url,
        "title": title,
        "summary": summary,
        "deadline_from_detail": deadline,
        "date_mentions": date_mentions,
        "fields": found,
        "missing": missing,
        "presentation": presented,
        "attachments": attachments.extract_links(html, url)[:5],
        "text_chars": len(text),
    }


# 발표(본선·피칭) 관련 문구 탐지. '참석 필수' 판정이 아니라 **문구가 있는지**만 본다.
# '발표'만으로 잡으면 "수상자 발표" 같은 네비게이션 문구가 걸려 오탐이 난다 → 복합 표현만 인정한다.
PRESENTATION_KEYWORDS = (
    "발표 심사",
    "발표심사",
    "발표 평가",
    "발표평가",
    "본선 발표",
    "발표 진행",
    "발표는",
    "대면 심사",
    "대면심사",
    "비대면",
    "피칭",
    "프레젠테이션",
    "발표 참석",
    "본선 진출",
)


def _presentation(text: str, summary: str) -> dict:
    body = f"{summary} {text}"
    for keyword in PRESENTATION_KEYWORDS:
        index = body.find(keyword)
        if index >= 0:
            return {
                "flag": "yes",
                "keyword": keyword,
                "evidence": body[max(0, index - 40) : index + 120].strip(),
            }
    return {"flag": "no", "keyword": None, "evidence": None}


def enrich_with_text(parsed: dict, text: str, *, source: str = "attachment") -> dict:
    """첨부파일 텍스트로 **비어 있는 항목만** 채운다. 이미 찾은 항목은 건드리지 않는다."""
    body = util.clean(text or "")
    if not body:
        return parsed
    fields = parsed.setdefault("fields", {})
    missing = parsed.setdefault("missing", [])
    for key, labels in LABELS.items():
        if key in fields:
            continue
        for label in labels:
            index = _find_label(body, label)
            if index is not None:
                fields[key] = {
                    "label": label,
                    "evidence": body[index : index + 240].strip(),
                    "source": source,
                }
                if key in missing:
                    missing.remove(key)
                break
    return parsed


def fetch_and_parse(fetcher, url: str, base=None) -> dict:
    """상세 페이지를 받아 파싱. 네트워크 오류는 예외로 올린다."""
    html = fetcher.get(url)
    return parse(html, url, base)
