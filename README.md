# contest-mcp

한국 공모전·대외활동을 **위비티 · 씽굿 · 올콘 · 링커리어 · 콘테스트코리아**에서 모아,
Claude Code / Cursor / Claude Desktop 같은 MCP 클라이언트에서 바로 조회·기록하는 MCP 서버입니다.
API 키가 필요 없고 로컬 sqlite 한 파일로 동작합니다.

기존 공모전 수집가 대부분은 "크롤러 → 정적 사이트"입니다. 이 프로젝트는 세 가지에 집중합니다.

1. **깊이** — 상세 페이지에서 *자격 · 시상내역 · 배점 · 제출물 · 발표 · 문의처*를 **원문 문장 그대로(evidence)** 뽑습니다.
   못 찾은 항목은 비워두고 `missing`에 넣습니다(추측하지 않음).
2. **변경 감지** — 매일 갱신하면서 **마감 연장·시상 변경·제목 수정**만 골라 기록합니다. 낸 공고가 조용히 바뀌는 걸 잡습니다.
3. **내 상태** — 이미 낸 공고(`mark submitted`)를 기억해 결과에서 표시하고, 자격은 프로필과 대조해 5축으로 판정합니다.

## 빠른 시작

```bash
git clone https://github.com/mytrashcan/contest-mcp && cd contest-mcp
uv venv .venv && uv pip install --python .venv/bin/python -e .
```

Claude Code에 등록:

```bash
claude mcp add contest-mcp -s user -- "$(pwd)/.venv/bin/contest-mcp"
```

그다음 클라이언트에서 "contest-mcp로 공모전 좀 모아서 브리핑해줘"라고 하면 됩니다.
처음이면 `refresh` → `digest` 순으로 호출됩니다.

## 툴 12개

| 툴 | 하는 일 |
|---|---|
| `search_contests` | 마감·분야·지역·검색어 조회. 같은 공고는 여러 소스에 있어도 1건으로 묶고 출처를 함께 반환. `sort=views_low`면 **조회 적은 순(경쟁 약함)** |
| `get_contest` | 상세 파싱: 자격·시상·배점·제출물·발표·문의 + 근거 문장. `include_attachments=True`면 첨부까지 파싱 |
| `refresh` | 소스에서 목록 재수집 (아래 5개 소스) |
| `changes` | 최근 감지된 **공고 변경**(마감 연장·시상 변경·제목 변경) |
| `digest` | 아침 브리핑: D-N 이내를 경쟁 약한 순으로 + 제한 문구·변경·자격 배지 |
| `deadlines` | 마감 일정 + `.ics` 캘린더, 상세 원문의 **발표일(추정)** 포함 |
| `mark` | 제출 이력: 상태·제출일·경로·**증빙 발급 여부**·결과 |
| `my_list` | 내가 표시한 공고. `missing_proof=True`면 증빙 미확인 제출건만 |
| `add_idea` / `ideas` | 내가 낸 아이디어(작품) 등록·조회 — 재출품 감지용 |
| `check_reuse` | **재출품 금지 조항** 탐지 + 내 아이디어 유사도 경고 |
| `check_eligibility` | 자격을 프로필과 대조해 **학교·지역·나이·신분·팀** 5축 pass/fail/unknown 판정 |

## 제출·증빙 이력 (`mark` · `my_list`)

상태만 남기던 기록을 제출 이력으로 확장했습니다. 지원서 증빙이 필요한 상황을 전제로 합니다.

```bash
python -m contest_mcp.cli mark wevity:112316 submitted \
  --submitted-at 2026-11-12 --channel email --proof no --result pending --note "메일 발송"
python -m contest_mcp.cli my-list --missing-proof     # 증빙(상장 등)이 확인 안 된 제출건만
```

- `proof`: `yes / no / unknown` — 상장·수상확인서 발급 여부
- `result`: `pending / announced / won / lost`
- 넘기지 않은 필드는 기존 값을 유지합니다(부분 갱신).
- 구버전 DB는 열 때 **컬럼을 자동 추가**하고 기존 데이터는 그대로 둡니다.

## 재출품 위험 경고 (`add_idea` · `check_reuse`)

마감이 임박할수록 기존 작품을 재포장하고 싶어지는데, 그게 정확히 실격 축입니다.

```bash
python -m contest_mcp.cli idea add "아차맵 블랙박스 위험구간 지도" --contest-id wevity:112316
python -m contest_mcp.cli reuse contestkorea:202610280017 --idea "아차맵 — 블랙박스 위험구간 지도"
# 판정: warn — 재출품 제한 조항이 있고, 내가 낸 아이디어와 유사한 것이 있습니다.
#   조항 [중복 신청]: ... 중복 신청 또는 중복 참가 지원은 불가함 ...
#   유사 아이디어 1.0: 아차맵 블랙박스 위험구간 지도
```

- 조항 탐지 패턴: 타 공모전·중복 응모/신청/참가·재출품·기출품·동일 작품·표절 등
- 판정 4단계: `warn`(조항+유사) / `clause_only` / `similar_only` / `clear`
- **첨부파일에만 있는 조항**도 잡습니다(`get_contest(include_attachments=True)`로 첨부 텍스트를 저장한 경우)

## 첨부 파싱 (`include_attachments`)

배점·제출물·발표 방식은 본문에 없고 첨부에만 있는 경우가 많습니다(실측: 배점 2/12).

- 지원 형식: **hwpx · docx**(zip+xml, 표준 라이브러리만) / **pdf**(`pdftotext`) / **zip 안의 문서** / hwp(`hwp5txt`·LibreOffice 있을 때)
- 이미 본문에서 찾은 항목은 건드리지 않고 **비어 있는 항목만** 채웁니다(`source: attachment` 표시)
- 파서가 없으면 조용히 넘어가지 않고 `error`에 이유를 남깁니다
- 한계: **씽굿 첨부는 `href="javascript:void(0)"` JS 다운로드**라 자동 수집이 불가합니다(링크가 아니라 앵커 텍스트 확장자로도 판별하지만, JS면 건너뜁니다)

## 발표 문구 플래그

`presentation.flag`는 **발표 관련 문구가 원문에 있는지**만 봅니다(참석 필수 판정 아님).

- 인정 표현: `발표 심사`·`발표평가`·`본선 발표`·`발표는`·`대면 심사`·`비대면`·`피칭`·`프레젠테이션`·`본선 진출`
- `발표` 단독은 네비게이션("수상자 발표")에 걸려 오탐이 나서 제외했습니다(실측으로 잡은 문제)

## 변경 감지 (`changes`)

`refresh`가 갱신할 때 **이전 값과 달라진 필드만** `change_log`에 남깁니다.
대상은 마감일·접수시작·시상·제목 4개이고, 조회수와 현황 문구(접수중→마감임박)는 매번 흔들려서 제외합니다.

- 마감 연장(`2026-11-20 → 2026-11-27`), 시상 내역 변경, 제목 수정이 잡힙니다.
- 같은 전이(20→27)는 한 번만 기록되고, 되돌림(27→20)은 별도로 기록됩니다.
- 상세 원문 날짜가 배지 날짜를 이기는 경우는 `basis=detail`로 남겨 '변경'이 아니라 **정정**임을 구분합니다.

## 아침 브리핑 (`digest`)

프로필 없이도 동작합니다. **조회수가 낮은(= 경쟁이 약한) 순**으로 D-N 후보를 세우고
`조회 등급 · 제한 문구 · 변경 · 자격` 배지를 붙입니다.

```
[contest-mcp] 2026-11-13 기준 D-7 이내 4건
1. D-5 · 제1회 순천시 청년정책 숏폼 공모전
   조회 3 (낮음) · 제한: 참가자격 대학생 대학원생 일반인
2. D-4 · ...

최근 7일 변경 11건
  - 제14회 한옥디자인 국제 공모전: 마감 2026-11-01→2026-12-28
```

프로필이 있으면 `자격 O` / `자격 X(region)` 배지가 추가됩니다.
축을 하나도 확정하지 못한 공고는 배지를 붙이지 않습니다(전부 `?`면 정보가 아니므로).

## 자격 판정 (`check_eligibility`)

공고의 "지원 자격" 문장과 내 조건을 비교하려면 내 조건이 어딘가 적혀 있어야 합니다.
그 5줄짜리 메모가 프로필 파일입니다. **없어도 도구는 정상 동작**하며, 그때 판정은 `unknown`으로 남습니다.

```bash
cp profile.example.json ~/.contest-mcp/profile.json
# 열어서 내 값으로 고치기 (예제 값은 자리표시자입니다)
```

| 키 | 뜻 | 예 |
|---|---|---|
| `school` | 다니는 학교 | `경북대학교` |
| `region` | **주민등록지** 기준 광역 지역 (학교 소재지 아님) | `경북` |
| `status` | 신분 | `학부생` / `대학원생` / `일반인` |
| `age` | 만 나이 (숫자) | `24` |
| `team_available` | 함께 낼 팀을 꾸릴 수 있는지 | `true` |

원칙은 **근거 없으면 unknown** 입니다.

- `fail`은 원문에 명시적 제한이 있고 프로필과 어긋날 때만. 모든 판정에 원문 문장이 붙습니다.
- **지역은 주민등록지 기준**입니다. 학교 소재지로 갈음되지 않습니다 — 예: "경북도민" 자격에 대구 거주자는 미달.
- 시·군 단위 제한("포항 거주")은 광역 지역과 직접 비교하지 않고 unknown으로 남깁니다.
- 기관·회사 이름에 들어간 지명(예: `경북ICT융합산업진흥협회`)은 자격 조건으로 읽지 않습니다.

## 마감·캘린더 (`deadlines`)

- 마감 이벤트와 함께 상세 원문에서 찾은 **발표일(추정)** 을 `[발표?]` 라벨로 넣습니다. 근거 문장은 설명에 붙습니다.
- `.ics` 파일을 만들면 캘린더 앱에서 두 번 클릭으로 추가됩니다(종일 이벤트, 기본 2일 전 알림).
- 날짜가 없는 공고는 이벤트를 만들지 않습니다.

## 지원 소스

| 소스 | 방식 | 마감일 근거 |
|---|---|---|
| 위비티 | 서버 렌더 HTML 목록 | D-day 배지 (**-1일 보정**: 배지가 마감일 당일을 포함) |
| 씽굿 | `subList.do` POST JSON | 목록의 실제 날짜 |
| 올콘 | `ajax.contest_list.php` POST JSON | 목록의 실제 날짜 |
| 링커리어 | Next.js 데이터 라우트(`_next/data/<buildId>/list/contest.json`) | `recruitCloseAt` epoch |
| 콘테스트코리아 | 서버 렌더 HTML 목록 | D-day 배지 (**보정 없음** — 배지 D-2의 상세 원문이 오늘+2) |

소스마다 D-day 규칙이 **반대**라는 점이 이 프로젝트에서 가장 조심한 부분입니다. 배지를 그대로 날짜로 바꾸면
하루씩 틀리므로, 소스별로 상세 1건을 열어 방향을 확인한 뒤 코드에 고정했습니다. 상세 원문의 기간이 있으면
그 값이 우선합니다(`apply_end_basis = detail`).

## 중복 병합

두 단계로 묶습니다.

1. 이름 정규화(연도·괄호·기호 제거) 해시가 같으면 같은 공고.
2. **의미 토큰 포함관계** — 주최명이 제목 앞에 붙어 이름이 달라진 경우
   (`한국보건의료정보원 2026년 제6회 진료정보교류 …` ⊃ `제6회 진료정보교류 …`).
   짧은 이름의 오병합을 막으려고 토큰 3개 이상일 때만 적용합니다.
   다른 부문(청소년부/대학부)처럼 토큰 집합이 서로를 포함하지 않으면 별개로 남습니다.

실측(5소스 × 3페이지): 원본 212행 → **173건**으로 묶임(중복 39행 제거), 2개 이상 출처로 묶인 그룹 33건.

## 데이터 커버리지 (정직한 수치)

상세까지 파싱한 표본 12건 기준입니다. 표본이 작아 참고용입니다.

| 항목 | 잡힌 비율 |
|---|---|
| 시상내역 | 12/12 |
| 문의처 | 11/12 |
| 자격 | 10/12 |
| 제출물 | 8/12 |
| 배점 | 2/12 |
| 발표 필참 | 1/12 |
| 발표일 언급 | 5/12 |

배점·발표 필참은 원문이 표나 첨부(hwp/pdf)에 있는 경우가 많아 목록·본문 파싱만으로는 부족합니다.
지금은 **없으면 없다고 반환**하고, 그 항목에 기대는 기능(발표 필참 필터 등)은 넣지 않았습니다.

## 저장 구조

sqlite 한 파일(`~/.contest-mcp/db.sqlite`)에 4개 테이블.

| 테이블 | 내용 |
|---|---|
| `contest` | 수집된 공고(소스별 1행) + 상세 파싱 캐시 + 마감일 근거(`apply_end_basis`) |
| `my_list` | 내 상태 + 제출일·경로·증빙·결과 |
| `idea` | 내가 낸 아이디어(토큰·낸 공고 목록) — 재출품 감지용 |
| `change_log` | 필드별 변경 이력(같은 전이 중복 방지) |
| `source_run` | 소스별 마지막 실행 결과(수집 수·오류) |

환경변수: `CONTEST_MCP_DB`(DB 경로), `CONTEST_MCP_PROFILE`(프로필 경로). API 키는 쓰지 않습니다.

## CLI (MCP 없이)

```bash
export PYTHONPATH=src
python -m contest_mcp.cli refresh --pages 5                    # 수집
python -m contest_mcp.cli digest --days 7                      # 아침 브리핑
python -m contest_mcp.cli search --query AI --dday 30          # 검색
python -m contest_mcp.cli search --sort views_low --dday 30    # 경쟁 약한 순
python -m contest_mcp.cli detail wevity:112316                 # 상세 원문 파싱
python -m contest_mcp.cli detail contestkorea:202610280017 --attachments  # 첨부까지
python -m contest_mcp.cli check wevity:112316                  # 자격 판정
python -m contest_mcp.cli reuse wevity:112316 --idea "아차맵"   # 재출품 조항·유사 아이디어
python -m contest_mcp.cli idea add "아차맵" --contest-id wevity:112316
python -m contest_mcp.cli my-list --missing-proof              # 증빙 미확인 제출건
python -m contest_mcp.cli changes --days 7                     # 최근 변경
python -m contest_mcp.cli deadlines --days 14 --ics ~/Downloads/contest.ics
python -m contest_mcp.cli mark wevity:112316 submitted --note "11/12 접수"
python -m contest_mcp.cli my-list --state submitted
```

`--json`을 붙이면 구조화 출력입니다.

## 설계 메모

- **수집 예절**: 요청 전 `robots.txt` 확인(Disallow면 그 소스를 건너뛰고 이유를 남김), 호스트별 최소 1초 간격.
  `www.all-con.co.kr`은 인증서 체인이 불완전해 검증 없이 수집하며, 그 사실을 결과의 `warnings`에 표시합니다.
- **씽굿·올콘은 JSON API**: 씽굿 `subList.do`(POST JSON), 올콘 `ajax.contest_list.php`(폼 POST +
  `X-Requested-With`). 브라우저 없이 수집되고 행에 자격·상금·현황이 들어 있습니다.
- **mcp 1.x/2.x 호환**: 2.x에서 `FastMCP`가 `MCPServer`로 개명되어, 둘 다 지원하도록 분기했습니다.
- **의존성**: Python 3.11+, `mcp` 하나. 파서·DB·ICS·판정은 표준 라이브러리만 사용합니다.

## 왜 이벤터스·기업마당은 없나

빼둔 게 아니라 **지금 방식으로는 못 받습니다**(2026-11 실측).

- **이벤터스**: 공개 API(`api.event-us.kr/api/v1/events`)가 **401**(키 필요). 사이트는 SPA라 HTML에 카드가 없습니다.
- **기업마당**: 공공데이터포털 지원사업 데이터가 **오픈API가 아니라 파일(CSV) 배포**이고 갱신 주기가 연 1회입니다.
  `apis.data.go.kr/1421000/bizinfo/pblancList` 같은 경로는 현재 `NO_OPENAPI_SERVICE_ERROR`를 돌려줍니다.

## 한계

- 이벤터스·기업마당·K-Startup 등 나머지 소스 (위 참조)
- 자격 판정은 규칙 기반 — 시·군 단위 제한, 트랙별 예외, 서류 요건은 `unknown`
- 발표일은 원문 문장에서 뽑은 **추정값**(`[발표?]`). 상세를 한 번도 열지 않은 공고는 없음
- **씽굿 첨부는 JS 다운로드**(`javascript:void(0)`)라 자동 수집 불가 → 그 소스는 본문 파싱만
- pdf·hwp 첨부는 `pdftotext`/`hwp5txt`(또는 LibreOffice)가 설치된 환경에서만 파싱된다. 없으면 `error`로 남는다
- 배점·발표 필참은 커버리지가 낮아 관련 **필터**는 제공하지 않는다(문구 플래그·근거 문장만 제공)
- 원격 호스팅/OAuth 없음 — 1인 로컬(stdio) 기준

## 개발

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest     # 94 tests — 네트워크 없이 파서·DB·ICS·자격·변경·재출품·첨부 검증
.venv/bin/python -m ruff check src tests
```

CI(GitHub Actions)는 Python 3.11 / 3.12 / 3.13에서 같은 검사를 돌립니다.

## 라이선스

MIT. 수집 대상 사이트의 약관·robots.txt를 존중하며, 데이터의 권리는 각 주최·플랫폼에 있습니다.
