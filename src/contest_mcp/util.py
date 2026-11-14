"""텍스트 정규화·날짜 파싱·그룹핑 헬퍼.

날짜 규칙은 실측에 근거한다:
- 위비티의 ``D-n`` 배지는 마감일 당일을 포함해 세므로 실제 마감일보다 하루 늦다.
  (외부 수집기가 공개 데이터 96건을 대조해 91건에서 하루 차이를 측정했다.)
  따라서 배지→날짜 변환은 ``-1일`` 보정하고, 상세 페이지의 원문 날짜가 있으면 그쪽을 쓴다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_GROUP_NOISE = re.compile(r"\[[^\]]{0,24}\]|\([^)]{0,24}\)")
_NON_NAME = re.compile(r"[^0-9a-z가-힣]+")

_FULL = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_SHORT = re.compile(r"(?<!\d)(\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)")
_MONTH_DAY = re.compile(r"(?<![\d.])(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?![\d.])")
_DDAY = re.compile(r"D\s*[-‐]\s*(\d{1,3})", re.I)
_DDAY_TODAY = re.compile(r"D\s*[-‐]\s*(?:DAY|0)\b", re.I)

class EmptyListing(RuntimeError):
    """첫 페이지에서 한 건도 읽지 못했다 — 사이트 구조가 바뀌었을 가능성이 크다."""


def ensure_first_page(label: str, page: int) -> None:
    """목록이 비었을 때 호출한다. 첫 페이지라면 '끝'이 아니라 '파서가 깨짐'이므로 오류로 올린다.

    조용히 0건을 돌려주면 refresh 결과가 정상처럼 보여 소스가 죽은 걸 한참 뒤에야 알게 된다.
    """
    if page == 1:
        raise EmptyListing(f"{label}: 첫 페이지에서 0건 — 사이트 구조 변경 또는 차단 의심")


def today(tz: ZoneInfo = SEOUL) -> dt.date:
    """서울 기준 오늘."""
    return dt.datetime.now(tz).date()


def clean(text: object) -> str:
    """HTML 태그·엔티티·연속 공백을 정리한 평문."""
    if text is None:
        return ""
    # 엔티티는 전부 푼다(&#8226; · &ldquo; · &#x2F; 등). 몇 개만 풀면 나머지가 제목 병합·조항 탐지에 섞인다.
    out = html.unescape(str(text))
    out = _TAG.sub(" ", out)
    out = out.replace("\ufeff", "").replace("\u200b", "")
    return _WS.sub(" ", out).strip()


def norm_name(name: str) -> str:
    """중복 판정용 이름 정규화 (연도·괄호·기호 제거)."""
    text = clean(name).lower()
    text = _GROUP_NOISE.sub(" ", text)
    return _NON_NAME.sub("", text)


def group_id(name: str) -> str:
    """같은 공고가 여러 소스에 있어도 묶이도록 만드는 키."""
    normalized = norm_name(name)
    return hashlib.sha1(normalized.encode()).hexdigest()[:12] if normalized else ""


# 병합 판정에서 무시하는 토큰: 어디에나 붙는 행정·홍보 단어와 연도·회차 표기.
_STOPWORDS = frozenset(
    {
        "공모",
        "공모전",
        "대회",
        "경진대회",
        "모집",
        "공고",
        "안내",
        "참가",
        "참여",
        "신청",
        "접수",
        "개최",
        "이벤트",
        "부문",
        "대상",
        "작품",
        "콘테스트",
        "챌린지",
        "시즌",
    }
)
_TOKEN = re.compile(r"[0-9a-z]+|[가-힣]+")
_YEAR = re.compile(r"^(?:20\d{2}|19\d{2})$")
_ROUND = re.compile(r"^제?\d+회?$")


def name_tokens(name: str) -> set[str]:
    """병합 판정용 의미 토큰. 연도·회차·홍보성 단어를 버린다."""
    tokens: set[str] = set()
    for token in _TOKEN.findall(clean(name).lower()):
        if len(token) < 2 or token in _STOPWORDS or _YEAR.match(token) or _ROUND.match(token):
            continue
        tokens.add(token)
    return tokens


def parse_date(value: object, base: dt.date | None = None) -> str | None:
    """여러 표기를 ISO 날짜로. 해석 불가면 None.

    ``2026-11-20`` · ``2026.11.20`` · ``2026년 11월 20일`` · ``26.11.20`` · ``11.20``(base 연도) 지원.
    """
    if value is None:
        return None
    text = clean(value)
    if not text:
        return None
    base_year = (base or today()).year
    for pattern, year_hint in ((_FULL, None), (_SHORT, 2000), (_MONTH_DAY, base_year)):
        match = pattern.search(text)
        if not match:
            continue
        parts = [int(g) for g in match.groups()]
        if year_hint is None:
            year, month, day = parts
        elif year_hint == 2000:
            year, month, day = 2000 + parts[0], parts[1], parts[2]
        else:
            year, month, day = year_hint, parts[0], parts[1]
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def parse_period(value: object, base: dt.date | None = None) -> tuple[str | None, str | None]:
    """``26.08.10~26.12.11`` · ``2026.11.2 ~ 11.16`` 같은 기간 문자열을 (시작, 끝)으로."""
    if value is None:
        return (None, None)
    text = clean(value)
    if not text:
        return (None, None)
    head, _, tail = text.partition("~")
    if not tail:
        dates = [m.group(0) for m in _FULL.finditer(text)] or [m.group(0) for m in _SHORT.finditer(text)]
        if len(dates) >= 2:
            return (parse_date(dates[0], base), parse_date(dates[1], base))
        return (parse_date(text, base), None)
    start = parse_date(head, base)
    end = parse_date(tail, base)
    if start and end and end < start:
        # 연도가 생략된 기간(2026.12.20~01.05)은 해를 넘긴 것으로 본다.
        bumped = parse_date(tail, dt.date(dt.date.fromisoformat(start).year + 1, 1, 1))
        end = bumped or end
    return (start, end)


def from_dday(text: object, base: dt.date | None = None) -> str | None:
    """``D-18`` → base+18일. ``D-DAY``/``D-0`` → base."""
    if text is None:
        return None
    raw = clean(text)
    ref = base or today()
    if _DDAY_TODAY.search(raw):
        return ref.isoformat()
    match = _DDAY.search(raw)
    if not match:
        return None
    return (ref + dt.timedelta(days=int(match.group(1)))).isoformat()


def wevity_deadline(badge: object, base: dt.date | None = None) -> str | None:
    """위비티 D-day 배지를 마감일로. 배지가 마감일 당일을 포함하므로 하루 당긴다."""
    iso = from_dday(badge, base)
    if not iso:
        return None
    if not _DDAY.search(clean(badge)):
        return iso
    return (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()


def status_of(
    apply_start: str | None,
    apply_end: str | None,
    base: dt.date | None = None,
) -> str:
    """open / upcoming / closed / unknown."""
    ref = (base or today()).isoformat()
    if apply_end and apply_end < ref:
        return "closed"
    if apply_start and apply_start > ref:
        return "upcoming"
    if apply_end:
        return "open"
    if apply_start:
        return "open"
    return "unknown"


def dday_of(apply_end: str | None, base: dt.date | None = None) -> int | None:
    """마감까지 남은 일수 (지난 경우 음수)."""
    if not apply_end:
        return None
    try:
        end = dt.date.fromisoformat(apply_end)
    except ValueError:
        return None
    return (end - (base or today())).days


def to_int(value: object) -> int | None:
    """``47,160`` 같은 조회수 문자열을 정수로."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None
