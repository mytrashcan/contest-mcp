"""응모 자격 문단을 '판정'으로 바꾼다 — 근거 문장이 있을 때만.

원칙: 단정하지 않는다.
- 명시적 제한을 찾으면 pass/fail, 못 찾으면 unknown + 왜 판정 불가인지.
- 모든 판정에는 원문에서 잘라낸 문장(evidence)을 붙인다. 심사·지원 판단은 사람이 한다.

프로필(``~/.contest-mcp/profile.json``)이 없으면 판정에 필요한 축을 unknown으로 돌려준다.
"""

from __future__ import annotations

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
_AGE_RANGE = re.compile(r"만\s*(\d{1,2})\s*세\s*[~\-]\s*(\d{1,2})\s*세")
_STUDENT = re.compile(r"(대학생|대학원생|학부생|고등학생|청소년|일반인|직장인|기업|소상공인)")
_TEAM_MIN = re.compile(r"(\d)\s*[~\-]\s*(\d)\s*인")
_TEAM_ONLY = re.compile(r"(\d)\s*인\s*이상\s*(?:팀|단체)")


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
    cond = _AGE_COND.search(text)
    if not cond:
        return _result("age", VERDICT_UNKNOWN, "연령 조건을 찾지 못함")
    limit, kind = int(cond.group(1)), cond.group(2)
    evidence = _sentence(text, cond.start())
    if not isinstance(age, int):
        return _result("age", VERDICT_UNKNOWN, f"만 {limit}세 {kind} 조건 — 프로필 age 없음", evidence)
    ok = {
        "이상": age >= limit,
        "이하": age <= limit,
        "미만": age < limit,
        "초과": age > limit,
    }[kind]
    verdict = VERDICT_PASS if ok else VERDICT_FAIL
    return _result("age", verdict, f"만 {limit}세 {kind} 조건 vs 내 나이 {age}", evidence)


def _check_status(text: str, profile: dict) -> dict:
    opening = _OPEN.search(text)
    if opening:
        evidence = _sentence(text, opening.start())
        return _result("status", VERDICT_PASS, "응모 대상이 열려 있음(제한 없음/누구나)", evidence)
    found = _STUDENT.search(text)
    if not found:
        return _result("status", VERDICT_UNKNOWN, "응모 대상(신분) 문구를 찾지 못함")
    label = found.group(1)
    mine = (profile.get("status") or "").strip()
    evidence = _sentence(text, found.start())
    if not mine:
        return _result("status", VERDICT_UNKNOWN, f"'{label}' 대상 — 프로필 status 없음", evidence)
    if label in {mine, "일반인"}:
        return _result("status", VERDICT_PASS, f"'{label}' 대상에 내 신분({mine})이 해당", evidence)
    if label in {"대학생", "학부생"} and mine in {"대학생", "학부생"}:
        return _result("status", VERDICT_PASS, "대학생 대상", evidence)
    if label in {"기업", "소상공인"} and mine not in {"기업", "소상공인"}:
        return _result("status", VERDICT_FAIL, f"'{label}' 대상 — 내 신분({mine})은 해당 없음", evidence)
    return _result("status", VERDICT_UNKNOWN, f"'{label}' 표기와 내 신분({mine}) 비교 불가", evidence)


def _check_team(text: str, profile: dict) -> dict:
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
