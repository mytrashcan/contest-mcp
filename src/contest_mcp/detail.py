"""상세 페이지 해석: 자격·시상·배점·제출물·발표·문의처를 '근거 문장'과 함께 뽑는다.

원칙: 없는 항목을 만들어 내지 않는다. 못 찾으면 ``missing`` 에 넣고, 찾은 항목은
원문에서 그대로 잘라낸 문장(``evidence``)을 함께 반환해 사람이 검증할 수 있게 한다.
목록 단계에서 확보한 마감일보다 상세 원문의 접수기간이 우선한다.
"""

from __future__ import annotations

import re

from . import attachments, reuse, util

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

# 근거 문장을 자를 때 쓰는 전체 라벨 목록(긴 라벨 우선 — '심사기준'보다 '제출서류'가 먼저 걸리지 않게).
_ALL_LABELS = tuple(sorted({alias for aliases in LABELS.values() for alias in aliases}, key=len, reverse=True))

META = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.S | re.I)
META_ALT = re.compile(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', re.S | re.I)
OG_TITLE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.S | re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
PERIOD = re.compile(r"(?:접수|모집|신청|응모)\s*기간[^0-9]{0,12}(.{0,80})", re.S)

# 표(``<th>라벨</th><td>값</td>``)는 라벨과 값이 구조로 묶여 있어 본문 텍스트보다 정확하다.
_TABLE_PAIR = re.compile(r"<th[^>]*>(.*?)</th>\s*<(?:td|dd)[^>]*>(.*?)</(?:td|dd)>", re.S | re.I)
PERIOD_LABELS = ("접수기간", "모집기간", "응모기간", "신청기간", "공모기간")
REGION_LABELS = ("대회지역", "개최지역", "행사지역", "지역구분", "지역")

# 사이트 공통 껍데기. 여기에 다른 공고의 발표일이 들어 있어 그대로 두면 이 공고의 날짜로 읽힌다.
_CHROME_TAGS = re.compile(r"<(script|style|noscript|nav|header|footer|aside)\b[^>]*>.*?</\1>", re.S | re.I)
_CHROME_CLASS_TOKENS = frozenset(
    {
        # 콘테스트코리아: 콘코알림 슬라이드·주메뉴·주간 추천 공모전 표·검색바
        "topnotice",
        "topnotice_slide",
        "top_search_wrap",
        "gnb",
        "gnb_menu",
        "gnb_menu_wrap",
        "s_gnb",
        "f-gnb",
        "gnb_dpe3",
        "total_menu",
        "total_menu_wrap",
        "advertising_menu",
        "weekly_cont",
        "weekly_monthly_cont",
        "weekly-tab-box",
        "list_style_2",
        "list_style_1",
        # 올콘: 상단 배너 슬라이드
        "main-banner",
        "splide",
        "splide__track",
        "splide__list",
        # 위비티: 광고 슬롯
        "kakao_ad_area",
    }
)
_OPEN_TAG = re.compile(r"<(div|section|ul|aside)\b([^>]*)>", re.I)
_CLASS_ATTR = re.compile(r"""(?:class|id)\s*=\s*["']([^"']*)["']""", re.I)
# 관례적으로 껍데기인 이름 접두사(링커리어 footer·헤더처럼 CSS-in-JS 해시가 붙는 경우를 잡는다).
_CHROME_PREFIXES = ("footer", "header", "gnb", "lnb")

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


def _is_chrome(attrs: str) -> bool:
    """여는 태그의 class/id 토큰이 사이트 껍데기 목록에 걸리는지."""
    for raw in _CLASS_ATTR.findall(attrs):
        for token in raw.lower().split():
            if token in _CHROME_CLASS_TOKENS or token.startswith(_CHROME_PREFIXES):
                return True
    return False


def _element_span(html: str, start: int, tag: str) -> int:
    """``start`` 의 여는 태그에 대응하는 닫는 태그 끝 위치(중첩 depth 계산)."""
    depth = 0
    opening = re.compile(rf"<(/?){tag}\b", re.I)
    for match in opening.finditer(html, start):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            end = html.find(">", match.end())
            return end + 1 if end != -1 else len(html)
    return len(html)


def strip_chrome(html: str) -> str:
    """사이트 공통 껍데기를 잘라낸 HTML.

    알림 슬라이드·주메뉴·추천 공모전 목록에는 **다른 공고의 발표일**이 들어 있다.
    걸러내지 않으면 그 날짜가 이 공고의 발표일처럼 읽혀 발표일 추적이 틀린다.
    """
    text = _CHROME_TAGS.sub(" ", html)
    pieces: list[str] = []
    cursor = 0
    for match in _OPEN_TAG.finditer(text):
        if match.start() < cursor or not _is_chrome(match.group(2)):
            continue
        pieces.append(text[cursor : match.start()])
        cursor = _element_span(text, match.start(), match.group(1))
    pieces.append(text[cursor:])
    return "".join(pieces)


# meta description 을 요약으로 쓸 수 있는 상한. 이보다 길면 본문 덤프로 본다(콘테스트코리아·위비티 실측 815~2216자).
SUMMARY_MAX = 400
_SUMMARY_NOISE = ("목록으로", "lightbox", "콘코알림", "회원 등급", "공고 바로가기", "SNS공유")


def _usable_summary(raw: str) -> str:
    """meta description 이 본문 덤프면 요약으로 쓰지 않는다(빈 문자열)."""
    text = util.clean(raw)
    if not text or len(text) > SUMMARY_MAX:
        return ""
    if any(token in text for token in _SUMMARY_NOISE):
        return ""
    return text


def _table_pairs(html: str) -> list[tuple[str, str]]:
    """``<th>라벨</th><td>값</td>`` 쌍을 (라벨, 값) 으로. 구조가 곧 근거다."""
    pairs: list[tuple[str, str]] = []
    for raw_label, raw_value in _TABLE_PAIR.findall(html):
        label = util.clean(raw_label)
        value = util.clean(raw_value)
        if label and value:
            pairs.append((label, value))
    return pairs


def _cut_at_next_label(chunk: str, leading: int = 0) -> str:
    """근거 문장이 다음 항목 라벨까지 넘어가지 않게 자른다(제출서류 → 심사기준 오염 방지).

    ``leading`` 은 문장 맨 앞에 붙은 자기 라벨 길이다. 거기서부터 찾으면 자기 라벨에서 잘린다.
    """
    cut = len(chunk)
    for alias in _ALL_LABELS:
        index = chunk.find(alias, max(leading, 1))
        if index != -1:
            cut = min(cut, index)
    return chunk[:cut].strip()


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
    """상세 HTML → 구조화된 요약 + 근거 문장.

    순서: 사이트 껍데기 제거 → 표(th/td) 구조 → meta 요약 → 본문 텍스트.
    구조(표)가 가장 믿을 만하고, 본문 텍스트는 라벨 뒤 문장을 잘라 쓴다.
    """
    ref = base or util.today()
    body = strip_chrome(html)
    text = util.clean(body)
    meta_match = META.search(html) or META_ALT.search(html)
    summary = _usable_summary(meta_match.group(1)) if meta_match else ""
    title_match = OG_TITLE.search(html) or TITLE.search(html)
    title = util.clean(title_match.group(1)) if title_match else ""

    # 본문에서 라벨을 찾아 뒤따르는 문장을 잘라낸다. 라벨 자체는 텍스트에서만 찾는다.
    # 표가 있으면 표를 먼저 본다(라벨-값이 구조로 묶여 있어 오염이 없다).
    found: dict[str, dict] = {}
    pairs = _table_pairs(body)
    for label, value in pairs:
        for key, labels in LABELS.items():
            if key in found:
                continue
            if any(alias in label for alias in labels):
                found[key] = {"label": label, "evidence": f"{label} {value}"[:240], "basis": "table"}

    if summary:
        haystacks = [summary, text]
        for key, labels in LABELS.items():
            if key in found:
                continue
            for label in labels:
                hit = None
                for source_text in haystacks:
                    index = _find_label(source_text, label)
                    if index is not None:
                        hit = _cut_at_next_label(source_text[index : index + 240].strip(), len(label))
                        break
                if hit:
                    found[key] = {"label": label, "evidence": hit}
                    break
    else:
        for key, labels in LABELS.items():
            if key in found:
                continue
            for label in labels:
                index = _find_label(text, label)
                if index is not None:
                    found[key] = {
                        "label": label,
                        "evidence": _cut_at_next_label(text[index : index + 240].strip(), len(label)),
                    }
                    break

    # 표의 '대회지역'은 구조화된 유일한 지역 근거다. 없으면 비워 둔다(추론하지 않는다).
    region = None
    for label, value in pairs:
        if label in REGION_LABELS:
            region = util.norm_region(value)
            if region:
                break

    deadline = None
    table_period = next((value for label, value in pairs if label in PERIOD_LABELS), None)
    period_match = PERIOD.search(table_period or "") or PERIOD.search(text) or PERIOD.search(summary)
    if period_match:
        _start, end = util.parse_period(period_match.group(1), ref)
        deadline = end

    # 발표·심사·시상은 "문장 속 날짜"로만 존재한다. 추론하지 않고 언급된 날짜와 문맥을 그대로 모은다.
    # 요약과 본문이 같은 문장을 담는 경우가 많아 (날짜, 라벨) 기준으로 중복을 버린다.
    date_mentions: list[dict] = []
    seen_mentions: set[tuple[str, str]] = set()
    mention_text = " ".join(part for part in (summary, text) if part)
    for match in _DATE_CONTEXT.finditer(mention_text):
        label = match.group(1)
        parsed = util.parse_date(match.group(2), ref)
        if not parsed or (parsed, label) in seen_mentions:
            continue
        seen_mentions.add((parsed, label))
        date_mentions.append(
            {
                "label": label,
                "kind": _kind_of(label),
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
        "region": region,
        "deadline_from_detail": deadline,
        "date_mentions": date_mentions,
        "fields": found,
        "missing": missing,
        "presentation": presented,
        # 재출품·중복 응모 조항은 보통 '유의사항'에 있어 필드별 근거 창에 안 잡힌다 → 본문 전체에서 따로 모은다.
        "reuse_clauses": reuse.detect_clauses(text),
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


def carry_attachment_text(parsed: dict, previous: dict | None) -> dict:
    """상세를 다시 받아도 이전에 첨부에서 얻은 텍스트와 그걸로 채운 항목을 잃지 않게 한다.

    새로 받은 결과에 이미 첨부 텍스트가 있으면(방금 첨부를 다시 받은 경우) 건드리지 않는다.
    """
    text = (previous or {}).get("attachment_text")
    if text and not parsed.get("attachment_text"):
        parsed["attachment_text"] = text
        enrich_with_text(parsed, text)
    return parsed


def fetch_and_parse(fetcher, url: str, base=None) -> dict:
    """상세 페이지를 받아 파싱. 네트워크 오류는 예외로 올린다."""
    html = fetcher.get(url)
    return parse(html, url, base)
