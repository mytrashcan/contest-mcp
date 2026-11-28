"""시간 이동 플러그인 — '오늘이 먼 미래일 때도 테스트가 통과하는가'를 검사한다.

왜 필요한가: 테스트 픽스처에 미래 날짜를 하드코딩하면 그 날짜가 지나간 뒤 조용히 깨진다.
날짜 리터럴을 정적으로 훑는 검사(date_bomb)는 **BASE를 고정 주입한 테스트**에서는 오탐이 된다
(날짜가 픽스처일 뿐 '오늘'에 의존하지 않는다). 실제로 이 저장소는 그런 형태다.

그래서 리터럴이 아니라 **행동**을 검사한다: 시계를 미래로 돌려 놓고 스위트를 그대로 돌린다.
`today()`에 의존하는 코드가 하나라도 있으면 여기서 깨진다.

실측(2026-11-28): 이 플러그인으로 2030-01-01을 재현했을 때
`util.parse_period("2026.12.20~01.05")` 가 (2026-12-20, **2030**-01-05) 를 돌려줬다 —
꼬리에 연도가 없을 때 `today()`의 연도를 쓰고 있었다. 사용자에게는 '해를 넘긴 기간'이
아닌 것으로 보이는 실제 버그였다. 고친 뒤 2028·2030·2035 모두 통과한다.

사용:
    TIMEWARP_YEAR=2030 pytest -q -p tests.timewarp_plugin
    TIMEWARP_YEAR=2035 pytest -q -p tests.timewarp_plugin

환경변수가 없으면 아무것도 하지 않는다(평소 실행에 영향 0).

주의: `datetime.datetime` 과 `datetime.date` 를 함께 바꾼다. `date.today()` 만 패치하면
`datetime.now().date()` 경로(이 저장소의 `util.today()`)를 놓친다 — 처음에 그렇게 만들어
패치가 안 먹은 채로 '통과'가 나왔다.
"""
from __future__ import annotations

import datetime as _dt
import os


def pytest_configure(config):
    year = os.environ.get("TIMEWARP_YEAR", "").strip()
    if not year.isdigit():
        return
    target = int(year)
    real_dt = _dt.datetime
    real_date = _dt.date

    class FakeDateTime(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(target, 6, 15, 12, 0, 0, tzinfo=tz)

        @classmethod
        def today(cls):
            return real_dt(target, 6, 15)

        @classmethod
        def utcnow(cls):
            return real_dt(target, 6, 15, 3, 0, 0)

    class FakeDate(real_date):
        @classmethod
        def today(cls):
            return real_date(target, 6, 15)

    _dt.datetime = FakeDateTime
    _dt.date = FakeDate
    try:
        import contest_mcp.util as util
        util.dt.datetime = FakeDateTime
        util.dt.date = FakeDate
    except ImportError:      # 테스트 수집 전에는 패키지가 아직 sys.path 에 없을 수 있다
        pass
    print(f"\n[timewarp] 오늘 = {target}-06-15 로 고정")
