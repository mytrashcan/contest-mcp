"""자격 판정 규칙 — 근거가 있을 때만 pass/fail, 나머지는 unknown."""

from __future__ import annotations

from contest_mcp import eligibility

PROFILE = {
    "school": "경북대학교",
    "region": "대구",
    "status": "학부생",
    "age": 24,
    "team_available": True,
}


def _axis(result: dict, name: str) -> dict:
    return next(axis for axis in result["axes"] if axis["axis"] == name)


def test_region_province_requirement_is_not_satisfied_by_school_location():
    """경북대는 대구 소재 → '경북 도민' 자격에는 걸리지 않는다(실측 함정)."""
    text = "참여대상: 경북도민 누구나, 경북 소재 직장인·학생 등"
    result = eligibility.check(text, PROFILE)
    assert _axis(result, "region")["verdict"] == "fail"
    assert result["overall"] == "fail"


def test_region_passes_for_matching_province():
    text = "참여대상: 경북도민 누구나"
    result = eligibility.check(text, {**PROFILE, "region": "경북"})
    assert _axis(result, "region")["verdict"] == "pass"


def test_region_multi_city_list_passes_when_included():
    text = "대구·경북 소재 대학 재학생"
    result = eligibility.check(text, PROFILE)
    assert _axis(result, "region")["verdict"] == "pass"


def test_school_whitelist_fails_for_other_school():
    text = "참가 자격: 서울대학교, 연세대학교, 고려대학교 재학생"
    result = eligibility.check(text, PROFILE)
    school = _axis(result, "school")
    assert school["verdict"] == "fail"
    assert "경북대학교" in school["reason"]


def test_school_whitelist_passes_when_my_school_listed():
    text = "참가 자격: 서울대학교, 경북대학교 재학생"
    result = eligibility.check(text, PROFILE)
    assert _axis(result, "school")["verdict"] == "pass"


def test_age_condition_compares_profile_age():
    assert _axis(eligibility.check("만 19세 이상 참가 가능", PROFILE), "age")["verdict"] == "pass"
    assert _axis(eligibility.check("만 19세 미만만 참가", PROFILE), "age")["verdict"] == "fail"
    assert _axis(eligibility.check("만 19세 이상 참가 가능", {}), "age")["verdict"] == "unknown"


def test_open_target_passes_status():
    result = eligibility.check("대한민국 국민 누구나 참여 가능", PROFILE)
    assert _axis(result, "status")["verdict"] == "pass"


def test_student_target_matches_undergraduate():
    result = eligibility.check("모집 대상: 대학생", PROFILE)
    assert _axis(result, "status")["verdict"] == "pass"


def test_team_requirement_fails_when_no_team():
    text = "2인 이상 팀만 응모 가능"
    assert _axis(eligibility.check(text, {**PROFILE, "team_available": False}), "team")["verdict"] == "fail"
    assert _axis(eligibility.check(text, PROFILE), "team")["verdict"] == "pass"


def test_without_profile_everything_is_unknown():
    result = eligibility.check("참가 자격: 대학생, 경북도민", {})
    assert result["overall"] == "unknown"
    assert set(result["unknown_axes"])


def test_empty_text_does_not_guess():
    result = eligibility.check("", PROFILE)
    assert result["overall"] == "unknown"
    assert all(axis["verdict"] == "unknown" for axis in result["axes"])


def test_evidence_is_attached_to_each_verdict():
    text = "지원 자격 : 국내 대학 재학생 / 2인 이상 팀 필수"
    result = eligibility.check(text, PROFILE)
    for axis in result["axes"]:
        if axis["verdict"] != "unknown":
            assert axis["evidence"], axis


def test_region_in_organization_name_is_not_a_restriction():
    """기관명에 들어간 지명(경북ICT융합산업진흥협회)을 지역 제한으로 오해하지 않는다."""
    text = "시상내역 - 대상 300만원 / 문의 사항 - 경북ICT융합산업진흥협회 미래성장본부"
    axis = _axis(eligibility.check(text, PROFILE), "region")
    assert axis["verdict"] == "unknown"
    assert "찾지 못함" in axis["reason"]


def test_city_level_restriction_is_reported_as_unknown():
    """시·군 단위 제한(포항 거주)은 프로필 광역 지역과 직접 비교하지 않는다."""
    axis = _axis(eligibility.check("지원 자격: 포항 거주 만 18세 이상 누구나", PROFILE), "region")
    assert axis["verdict"] == "unknown"
    assert "광역" in axis["reason"] or "시·군" in axis["reason"]
