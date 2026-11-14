"""응모 자격 문단을 '판정'으로 바꾼다 — 근거 문장이 있을 때만.

원칙: 단정하지 않는다.
- 명시적 제한을 찾으면 pass/fail, 못 찾으면 unknown + 왜 판정 불가인지.
- 모든 판정에는 원문에서 잘라낸 문장(evidence)을 붙인다. 심사·지원 판단은 사람이 한다.

프로필(``~/.contest-mcp/profile.json``)이 없으면 판정에 필요한 축을 unknown으로 돌려준다.
"""

from __future__ import annotations

import operator
import re

AXES = ("school", "region", "age", "status", "team")

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_UNKNOWN = "unknown"

REGIONS = (
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
)

_SCHOOL = re.compile(r"([가-힣A-Za-z]{2,10}대학교)")
_SCHOOL_RESTRICT = re.compile(r"(소속\s*대학|재학\s*중인\s*대학|대학별|학교별|협의회|컨소시엄)")
_OPEN = re.compile(r"(제한\s*없음|누구나|전\s*국민|성인\s*누구나)")
_REGION_TOKEN = re.compile("|".join(REGIONS))
_REGION_SUFFIX = re.compile(r"(도민|시민|거주|소재|주민등록|지역)")
_AGE_COND = re.compile(r"만?\s*(\d{1,2})\s*세\s*(이상|이하|미만|초과)")
_AGE_RANGE = re.compile(r"만\s*(\d{1,2})\s*세?\s*[~\-]\s*(?:만\s*)?(\d{1,2})\s*세")
_AGE_OK = {"이상": operator.ge, "이하": operator.le, "미만": operator.lt, "초과": operator.gt}
_STUDENT = re.compile(r"(대학생|대학원생|학부생|고등학생|청소년|일반인|직장인|기업|소상공인)")
_UNDERGRAD = frozenset({"대학생", "학부생"})
_BUSINESS = frozenset({"기업", "소상공인"})
# 신분 바로 뒤에 붙어 그 신분을 대상에서 빼는 표현("대학원생 제외", "일반인은 참가 불가").
_EXCLUDED_AFTER = re.compile(r"\s*(?:은|는|도)?\s*(?:제외|(?:참가|응모|지원|신청)\s*불가)")
_TEAM_MIN = re.compile(r"(\d)\s*[~\-]\s*(\d)\s*인")
_TEAM_ONLY = re.compile(r"(\d)\s*인\s*이상\s*(?:팀|단체)")

# '누구나' 바로 앞에 신분 한정어가 있으면 열린 대상이 아니다("대학생이라면 누구나").
_STATUS_QUALIFIER = re.compile(
    r"(대학생|대학원생|학부생|재학생|휴학생|고등학생|중학생|초등학생|학생"
    r"|청소년|청년|직장인|재직자|기업|소상공인|창업자)"
)
# 한정어가 있어도 범위를 끝까지 여는 표현("초등학생부터 일반인까지 누구나").
_OPEN_HINTS = ("일반인", "국민", "까지")
# '제한 없음' 앞에 다른 항목이 오면 신분이 아니라 그 항목 얘기다("전공 제한 없음").
_OTHER_LIMIT = re.compile(r"(전공|학년|연령|나이|지역|국적|주제|분야|형식|분량|인원|팀원|장르|소재)")
# 팀 인원이 적혀 있어도 개인 응모를 함께 허용하는 표현("개인 또는 팀(2~4인)").
_SEP = r"(?:또는|혹은|및|이나|/|·|,)"
_PAREN = r"(?:\([^)]{0,20}\))?"
_SOLO_OK = re.compile(
    rf"개인\s*{_PAREN}\s*{_SEP}\s*(?:팀|단체)|(?:팀|단체)\s*{_PAREN}\s*{_SEP}\s*개인"
)


def _sentence(text: str, index: int, width: int = 90) -> str:
    start = max(0, index - 24)
    return text[start : index + width].strip()


def _result(axis: str, verdict: str, reason: str, evidence: str = "") -> dict:
    return {"axis": axis, "verdict": verdict, "reason": reason, "evidence": evidence}


def _check_school(text: str, profile: dict) -> dict:
    schools = _SCHOOL.findall(text)
    restriction = _SCHOOL_RESTRICT.search(text)
    if not schools and not restriction:
        return _result("school", VERDICT_UNKNOWN, "학교 제한 문구를 찾지 못함")
    mine = (profile.get("school") or "").strip()
    named = sorted({school for school in schools if len(school) >= 4})
    evidence = _sentence(text, (restriction or _SCHOOL.search(text)).start())
    if not mine:
        listed = ", ".join(named[:3])
        return _result("school", VERDICT_UNKNOWN, f"특정 대학이 명시됨({listed}) — 프로필 school 없음", evidence)
    if any(mine in school or school in mine for school in named):
        return _result("school", VERDICT_PASS, "명시된 대학 목록에 내 학교가 있음", evidence)
    if restriction or len(named) >= 2:
        return _result("school", VERDICT_FAIL, f"명시된 대학 목록에 내 학교({mine}) 없음", evidence)
    single = named[0] if named else "-"
    return _result(
        "school",
        VERDICT_UNKNOWN,
        f"대학명이 1개만 보임({single}) — 주최 측 표기일 수 있음",
        evidence,
    )


def _region_mentions(text: str) -> list[tuple[str, int]]:
    """지역 토큰 중 '거주/도민/소재' 같은 자격 표현 근처에 있는 것만 남긴다.

    기관·회사 이름에 들어간 지명(예: 경북ICT융합산업진흥협회)을 자격 조건으로 오해하지 않기 위해서다.
    """
    found: list[tuple[str, int]] = []
    for match in _REGION_TOKEN.finditer(text):
        window = text[max(0, match.start() - 14) : match.end() + 14]
        if _REGION_SUFFIX.search(window):
            found.append((match.group(0), match.start()))
    return found


def _check_region(text: str, profile: dict) -> dict:
    mentions = _region_mentions(text)
    regions = []
    for name, _index in mentions:
        if name not in regions:
            regions.append(name)
    suffix = _REGION_SUFFIX.search(text)
    if not regions:
        if suffix:
            return _result(
                "region",
                VERDICT_UNKNOWN,
                "지역 조건이 있으나 광역(도·시) 단위가 아님 — 시·군·구 단위는 프로필 지역과 직접 비교 불가",
                _sentence(text, suffix.start()),
            )
        return _result("region", VERDICT_UNKNOWN, "지역 제한 문구를 찾지 못함")
    evidence = _sentence(text, mentions[0][1])
    mine = (profile.get("region") or "").strip()
    if suffix and suffix.group(1) in {"도민", "시민"} and len(regions) == 1:
        only = regions[0]
        if not mine:
            return _result("region", VERDICT_UNKNOWN, f"'{only}' 단위 자격 — 프로필 region 없음", evidence)
        if mine == only:
            return _result("region", VERDICT_PASS, f"{only} 단위 자격을 충족", evidence)
        return _result(
            "region",
            VERDICT_FAIL,
            f"'{only}' 단위 자격인데 내 지역은 {mine} (학교 소재지로 갈음되지 않음)",
            evidence,
        )
    if not mine:
        return _result("region", VERDICT_UNKNOWN, f"지역 언급({', '.join(regions[:3])}) — 프로필 region 없음", evidence)
    if mine in regions:
        return _result("region", VERDICT_PASS, f"내 지역({mine})이 대상 지역에 포함", evidence)
    listed = ", ".join(regions[:3])
    return _result("region", VERDICT_UNKNOWN, f"지역({listed}) 언급 — 내 지역과 직접 비교 불가", evidence)


def _check_age(text: str, profile: dict) -> dict:
    age = profile.get("age")
    rng = _AGE_RANGE.search(text)
    if rng:
        low, high = int(rng.group(1)), int(rng.group(2))
        evidence = _sentence(text, rng.start())
        if not isinstance(age, int):
            return _result("age", VERDICT_UNKNOWN, f"만 {low}~{high}세 조건 — 프로필 age 없음", evidence)
        if low <= age <= high:
            return _result("age", VERDICT_PASS, f"만 {low}~{high}세 조건 충족", evidence)
        return _result("age", VERDICT_FAIL, f"만 {low}~{high}세 조건에 내 나이({age})가 맞지 않음", evidence)
    # 조건을 전부 모은다. 첫 조건만 보면 "만 19세 이상 39세 이하"에서 상한을 놓친다.
    # 같은 문장이 요약·본문에 반복되므로 (나이, 종류)로 중복을 없앤다.
    conds: dict[tuple[int, str], int] = {}
    for match in _AGE_COND.finditer(text):
        conds.setdefault((int(match.group(1)), match.group(2)), match.start())
    if not conds:
        return _result("age", VERDICT_UNKNOWN, "연령 조건을 찾지 못함")
    label = ", ".join(f"만 {limit}세 {kind}" for limit, kind in conds)
    evidence = _sentence(text, min(conds.values()))
    if not isinstance(age, int):
        return _result("age", VERDICT_UNKNOWN, f"{label} 조건 — 프로필 age 없음", evidence)
    outcomes = [_AGE_OK[kind](age, limit) for limit, kind in conds]
    lowers = [limit for limit, kind in conds if kind in {"이상", "초과"}]
    uppers = [limit for limit, kind in conds if kind in {"이하", "미만"}]
    is_band = len(lowers) == 1 and len(uppers) == 1 and lowers[0] <= uppers[0]
    if len(conds) == 1 or is_band or all(outcomes) or not any(outcomes):
        verdict = VERDICT_PASS if all(outcomes) else VERDICT_FAIL
        return _result("age", verdict, f"{label} 조건 vs 내 나이 {age}", evidence)
    # 하한·상한 한 쌍이 아닌데 결과가 갈리면 부문별 조건일 수 있다(청소년부/일반부) → 단정하지 않는다.
    return _result("age", VERDICT_UNKNOWN, f"연령 조건이 여러 개({label}) — 부문별 조건일 수 있음", evidence)


def _open_target(text: str) -> re.Match[str] | None:
    """신분 제한이 없다는 표현을 찾되, 한정어가 붙은 '누구나'와 다른 항목의 '제한 없음'은 건너뛴다."""
    for match in _OPEN.finditer(text):
        before = text[max(0, match.start() - 12) : match.start()]
        word = match.group(1)
        if word.startswith("제한"):
            if _OTHER_LIMIT.search(before[-8:]):
                continue
        elif word == "누구나" and _STATUS_QUALIFIER.search(before):
            if not any(hint in before for hint in _OPEN_HINTS):
                continue
        return match
    return None


def _check_status(text: str, profile: dict) -> dict:
    opening = _open_target(text)
    if opening:
        evidence = _sentence(text, opening.start())
        return _result("status", VERDICT_PASS, "응모 대상이 열려 있음(제한 없음/누구나)", evidence)
    # 첫 단어만 보면 "대학생, 대학원생, 일반인"에서 뒤의 대상을 놓친다 → 나온 신분을 전부 모은다.
    allowed: list[str] = []
    excluded: list[str] = []
    first: int | None = None
    for match in _STUDENT.finditer(text):
        if first is None:
            first = match.start()
        bucket = excluded if _EXCLUDED_AFTER.match(text, match.end()) else allowed
        if match.group(1) not in bucket:
            bucket.append(match.group(1))
    if first is None:
        return _result("status", VERDICT_UNKNOWN, "응모 대상(신분) 문구를 찾지 못함")
    mine = (profile.get("status") or "").strip()
    evidence = _sentence(text, first)
    listed = ", ".join(allowed) or "-"
    if not mine:
        return _result("status", VERDICT_UNKNOWN, f"'{listed}' 대상 — 프로필 status 없음", evidence)
    same_as_me = _UNDERGRAD if mine in _UNDERGRAD else {mine}
    if same_as_me & set(excluded):
        # '제외'가 어느 범위에 걸리는지는 문장을 읽어야 안다 → fail로 단정하지 않는다.
        return _result("status", VERDICT_UNKNOWN, f"내 신분({mine})에 '제외' 문구가 붙어 있음 — 원문 확인", evidence)
    if same_as_me & set(allowed) or "일반인" in allowed:
        return _result("status", VERDICT_PASS, f"'{listed}' 대상에 내 신분({mine})이 해당", evidence)
    if allowed and set(allowed) <= _BUSINESS and mine not in _BUSINESS:
        return _result("status", VERDICT_FAIL, f"'{listed}' 대상 — 내 신분({mine})은 해당 없음", evidence)
    return _result("status", VERDICT_UNKNOWN, f"'{listed}' 표기와 내 신분({mine}) 비교 불가", evidence)


def _check_team(text: str, profile: dict) -> dict:
    solo = _SOLO_OK.search(text)
    if solo:
        return _result("team", VERDICT_PASS, "개인 응모 가능(개인 또는 팀)", _sentence(text, solo.start()))
    only = _TEAM_ONLY.search(text) or _TEAM_MIN.search(text)
    if not only:
        return _result("team", VERDICT_UNKNOWN, "팀 구성 조건을 찾지 못함")
    minimum = int(only.group(1))
    available = profile.get("team_available")
    evidence = _sentence(text, only.start())
    if minimum <= 1:
        return _result("team", VERDICT_PASS, "단독 응모 가능", evidence)
    if available is True:
        return _result("team", VERDICT_PASS, f"{minimum}인 이상 팀 구성 가능(프로필)", evidence)
    if available is False:
        return _result("team", VERDICT_FAIL, f"{minimum}인 이상 팀 필수 — 팀원 확보 필요", evidence)
    return _result("team", VERDICT_UNKNOWN, f"{minimum}인 이상 팀 조건 — 프로필 team_available 없음", evidence)


CHECKS = (_check_school, _check_region, _check_age, _check_status, _check_team)


def check(text: str, profile: dict | None = None) -> dict:
    """자격 문단 텍스트 → 축별 판정. 프로필이 비면 대부분 unknown이 정상이다."""
    body = text or ""
    data = profile or {}
    axes = [check_fn(body, data) for check_fn in CHECKS]
    fails = [axis for axis in axes if axis["verdict"] == VERDICT_FAIL]
    unknowns = [axis for axis in axes if axis["verdict"] == VERDICT_UNKNOWN]
    if fails:
        overall = VERDICT_FAIL
    elif unknowns:
        overall = VERDICT_UNKNOWN
    else:
        overall = VERDICT_PASS
    return {
        "overall": overall,
        "axes": axes,
        "failed_axes": [axis["axis"] for axis in fails],
        "unknown_axes": [axis["axis"] for axis in unknowns],
        "note": "원문 문장 근거만 제시합니다. 최종 판단은 공고 원문과 주최 문의로 확인하세요.",
    }
