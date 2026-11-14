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


def test_qualified_everyone_is_not_an_open_target():
    """'대학생이라면 누구나'는 열린 대상이 아니다 — 일반인에게 pass를 주면 안 된다."""
    text = "참가자격: 전국 대학생이라면 누구나"
    assert _axis(eligibility.check(text, {**PROFILE, "status": "일반인"}), "status")["verdict"] == "unknown"
    assert _axis(eligibility.check(text, PROFILE), "status")["verdict"] == "pass"


def test_everyone_up_to_general_public_is_still_open():
    text = "참가자격: 초등학생부터 일반인까지 누구나"
    assert _axis(eligibility.check(text, {**PROFILE, "status": "일반인"}), "status")["verdict"] == "pass"


def test_no_limit_on_another_item_is_not_open_status():
    text = "참가자격: 대학생 (전공 제한 없음)"
    assert _axis(eligibility.check(text, {**PROFILE, "status": "일반인"}), "status")["verdict"] == "unknown"


def test_age_lower_and_upper_bound_are_both_checked():
    text = "만 19세 이상 39세 이하 청년"
    assert _axis(eligibility.check(text, {**PROFILE, "age": 45}), "age")["verdict"] == "fail"
    assert _axis(eligibility.check(text, PROFILE), "age")["verdict"] == "pass"
    # 요약·본문에 같은 조건이 반복돼도 결과는 같다
    assert _axis(eligibility.check(f"{text} / {text}", PROFILE), "age")["verdict"] == "pass"


def test_age_range_without_first_unit():
    assert _axis(eligibility.check("만 19~39세 청년", {**PROFILE, "age": 45}), "age")["verdict"] == "fail"
    assert _axis(eligibility.check("만 19~39세 청년", PROFILE), "age")["verdict"] == "pass"


def test_age_conditions_per_division_stay_unknown():
    """부문별 연령(청소년부/일반부)을 한 사람에게 모두 적용해 fail을 내지 않는다."""
    text = "청소년부: 만 18세 이하 / 일반부: 만 19세 이상"
    assert _axis(eligibility.check(text, PROFILE), "age")["verdict"] == "unknown"


def test_individual_or_team_is_not_a_team_requirement():
    profile = {**PROFILE, "team_available": False}
    assert _axis(eligibility.check("개인 또는 팀(2~4인) 참가", profile), "team")["verdict"] == "pass"
    assert _axis(eligibility.check("팀(2~4인) 또는 개인 참가", profile), "team")["verdict"] == "pass"
    assert _axis(eligibility.check("2~4인 팀 구성 필수", profile), "team")["verdict"] == "fail"


def test_status_considers_every_listed_target():
    """첫 단어만 보지 않는다 — 나열된 대상 어디에든 내 신분이 있으면 pass."""
    text = "참가 대상: 대학생, 대학원생, 일반인"
    for status in ("학부생", "대학원생", "일반인"):
        assert _axis(eligibility.check(text, {**PROFILE, "status": status}), "status")["verdict"] == "pass"


def test_excluded_status_is_not_passed():
    text = "참가 대상: 대학생 및 일반인 (대학원생 제외)"
    assert _axis(eligibility.check(text, {**PROFILE, "status": "대학원생"}), "status")["verdict"] == "unknown"
    assert _axis(eligibility.check(text, PROFILE), "status")["verdict"] == "pass"


def test_business_only_target_fails_but_mixed_target_does_not():
    assert _axis(eligibility.check("참가 대상: 소상공인 및 기업", PROFILE), "status")["verdict"] == "fail"
    assert _axis(eligibility.check("참가 대상: 기업 또는 일반인", PROFILE), "status")["verdict"] == "pass"
