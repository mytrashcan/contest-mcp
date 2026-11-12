# contest-mcp

한국 공모전·대외활동을 **위비티 · 씽굿 · 올콘**에서 수집해, Claude/Cursor 같은 MCP 클라이언트에서
바로 조회·기록할 수 있게 해 주는 MCP 서버입니다.

기존 공모전 수집가들은 대부분 "크롤러 → 정적 사이트"입니다. 이 프로젝트는 다르게 두 가지에 집중합니다.

1. **깊이** — 목록만 모으지 않고 상세 페이지에서 *자격 · 시상내역 · 배점 · 제출물 · 발표 필참 · 문의처*를
   **원문 문장 그대로(evidence)** 뽑아 줍니다. 못 찾은 항목은 비워두고 `missing`에 넣습니다(추측하지 않음).
2. **내 상태** — 이미 낸 공고(`mark submitted`)를 기억해 검색 결과에서 표시합니다. 같은 대회를 다시 추천하지 않습니다.

## 툴 5개

| 툴 | 하는 일 |
|---|---|
| `search_contests` | 마감·분야·지역·검색어로 조회. 같은 공고가 여러 소스에 있으면 1건으로 묶고 출처를 함께 반환. `sort=views_low` 면 **조회 적은 순(경쟁 약함)** |
| `get_contest` | 상세 파싱: 자격·시상·배점·제출물·발표·문의 + 근거 문장 + 발표일 언급 |
| `refresh` | 소스에서 목록 재수집 (아래 5개 소스) |
| `changes` | 최근 감지된 **공고 변경**(마감 연장·시상 변경·제목 변경) |
| `digest` | 아침 브리핑: D-N 이내를 경쟁 약한 순으로 + 제한 문구·변경·자격 배지 |
| `deadlines` | 마감 일정 모으기, 옵션으로 `.ics` 캘린더 생성. 상세 원문에서 찾은 **발표일(추정)** 도 함께 넣음 |
| `mark` / `my_list` | 제출·폐기·관심없음 기록과 조회 |
| `check_eligibility` | 공고 자격을 내 프로필과 대조해 **학교·지역·나이·신분·팀** 5축으로 pass/fail/unknown 판정 |

## 변경 감지 (`changes`)

`refresh`가 매일 돌면서 **이전 값과 달라진 필드만** `change_log`에 남깁니다.
대상은 마감일·접수시작·시상·제목 4개이고, 조회수·현황 문구는 매번 흔들려서 제외합니다.

- 마감 연장(11/20 → 11/27), 상시 변경되는 시상 내역, 제목 수정이 잡힙니다.
- 같은 전이(20→27)는 한 번만 기록되고, 되돌림(27→20)은 따로 기록됩니다.
- 상세 원문 날짜가 배지 날짜를 이기는 경우는 `basis=detail`로 표시해 '변경'이 아니라 '정정'임을 구분합니다.

## 아침 브리핑 (`digest`)

프로필 없이도 동작합니다. **조회수가 낮은(= 경쟁이 약한) 순**으로 D-N 후보를 세우고,
각 항목에 `조회 등급 · 제한 문구 · 변경 · 자격` 배지를 붙입니다.

```
[contest-mcp] 2026-11-13 기준 D-7 이내 4건
1. D-5 · 제1회 순천시 청년정책 숏폼 공모전
   조회 3 (낮음) · 제한: 참가자격 대학생 대학원생 일반인
2. D-4 · ...
```

프로필이 있으면 `자격 O` / `자격 X(region)` 배지가 추가됩니다. 축을 하나도 확정하지 못한 공고는
배지를 붙이지 않습니다(전부 `?`면 정보가 아니므로).

## 자격 판정 (`check_eligibility`)

공고의 "지원 자격" 문장과 **내 조건**을 비교하려면, 내 조건을 어딘가 적어둬야 합니다.
그 5줄짜리 메모가 프로필 파일입니다. 없으면 판정은 전부 `unknown`으로 나옵니다(틀린 판정보다 낫습니다).

```bash
cp profile.example.json ~/.contest-mcp/profile.json
# 그리고 ~/.contest-mcp/profile.json 을 열어 내 값으로 고치기
```

| 키 | 뜻 | 예 |
|---|---|---|
| `school` | 다니는 학교 | `경북대학교` |
| `region` | **주민등록지** 기준 광역 지역 (학교 소재지 아님) | `대구` |
| `status` | 신분 | `학부생` / `대학원생` / `일반인` |
| `age` | 만 나이 (숫자) | `24` |
| `team_available` | 함께 낼 팀을 꾸릴 수 있는지 | `true` |

원칙은 **근거 없으면 unknown** 입니다.

- `fail`은 원문에 명시적 제한이 있고 내 프로필과 어긋날 때만. 모든 판정에 원문 문장(evidence)이 붙습니다.
- **지역은 주민등록지 기준**입니다. 학교 소재지로 갈음되지 않습니다 — 예: "경북도민" 자격에 대구 거주자는 미달.
- 시·군 단위 제한("포항 거주")은 광역 지역과 직접 비교하지 않고 unknown으로 남깁니다.
- 기관·회사 이름에 들어간 지명(예: `경북ICT융합산업진흥협회`)은 자격 조건으로 읽지 않습니다.

## 지원 소스

| 소스 | 방식 | 마감일 근거 |
|---|---|---|
| 위비티 | 서버 렌더 HTML 목록 | D-day 배지 (**-1일 보정**: 배지가 마감일 당일을 포함) |
| 씽굿 | `subList.do` POST JSON | 목록의 실제 날짜 |
| 올콘 | `ajax.contest_list.php` POST JSON | 목록의 실제 날짜 |
| 링커리어 | Next.js 데이터 라우트(`_next/data/<buildId>/list/contest.json`) | `recruitCloseAt` epoch |
| 콘테스트코리아 | 서버 렌더 HTML 목록 | D-day 배지 (**보정 없음** — 배지 D-2의 상세 원문이 오늘+2) |

소스마다 D-day 규칙이 다르다는 점이 이 프로젝트에서 가장 조심한 부분입니다. 배지를 그대로 날짜로
바꾸면 하루씩 틀리고, 상세 페이지 원문의 기간이 있으면 그 값이 우선합니다(`apply_end_basis = detail`).

## 설치

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e .
```

Claude Code:

```bash
claude mcp add contest-mcp -s user -- /path/to/contest-mcp/.venv/bin/contest-mcp
```

Cursor / Claude Desktop (`mcpServers`):

```json
{
  "mcpServers": {
    "contest-mcp": {
      "command": "/path/to/contest-mcp/.venv/bin/contest-mcp"
    }
  }
}
```

DB는 기본 `~/.contest-mcp/db.sqlite`이고 `CONTEST_MCP_DB`로 바꿀 수 있습니다.

## MCP 없이 쓰기 (CLI)

```bash
export PYTHONPATH=src
python -m contest_mcp.cli refresh --pages 5          # 수집
python -m contest_mcp.cli search --query AI --dday 30 # 검색
python -m contest_mcp.cli search --sort views_low --dday 30   # 경쟁 약한 순
python -m contest_mcp.cli detail wevity:112316        # 상세 원문 파싱
python -m contest_mcp.cli check wevity:112316         # 자격 판정
python -m contest_mcp.cli changes --days 7            # 최근 변경(마감 연장 등)
python -m contest_mcp.cli digest --days 7             # 아침 브리핑
python -m contest_mcp.cli deadlines --days 14 --ics ~/Downloads/contest.ics
python -m contest_mcp.cli mark wevity:112316 submitted --note "11/12 접수"
```

## 설계 메모

- **위비티 마감일 보정**: 위비티는 목록에 `D-n` 배지만 주고, 배지가 마감일 당일을 포함해 셉니다.
  공개 데이터 96건 대조에서 91건이 하루 차이였습니다. 그래서 배지를 날짜로 바꿀 때 **-1일** 하고,
  상세 페이지 원문의 모집기간이 있으면 그 값으로 승격합니다(`apply_end_basis = detail`).
- **씽굿·올콘은 JSON API**: 씽굿 `subList.do`(POST JSON), 올콘 `ajax.contest_list.php`(폼 POST +
  `X-Requested-With`). 둘 다 브라우저 없이 수집되고, 행에 자격·상금·현황 필드가 들어 있습니다.
- **수집 예절**: 요청 전 `robots.txt` 확인(Disallow면 그 소스를 건너뛰고 이유를 남김), 호스트별 최소 1초 간격.
  `www.all-con.co.kr`은 인증서 체인이 불완전해 검증 없이 수집하며, 그 사실을 결과의 `warnings`에 표시합니다.
- **중복 병합**: 두 단계로 묶습니다. ① 이름 정규화(연도·괄호·기호 제거) 해시 동일, ② **의미 토큰 포함관계**
  (주최명이 제목 앞에 붙어 이름이 달라진 경우). 짧은 이름의 오병합을 막으려고 토큰 3개 이상일 때만
  ②를 적용합니다. 다른 부문(청소년부/대학부)처럼 토큰 집합이 서로를 포함하지 않으면 별개로 남습니다.
- **발표일**: 상세 원문에 문장으로만 존재하므로 추론하지 않고, 날짜 **언급**과 문맥을 그대로 반환합니다.

## 왜 이벤터스·기업마당은 없나

빼둔 게 아니라 **지금 방식으로는 못 받습니다**(2026-11 실측).

- **이벤터스**: 공개 API(`api.event-us.kr/api/v1/events`)가 **401**을 돌려줍니다(키 필요). 사이트는 SPA라
  HTML에 카드가 없습니다. 키를 얻으면 붙일 수 있는 구조입니다.
- **기업마당**: 공공데이터포털의 지원사업 데이터는 **오픈API가 아니라 파일(CSV) 배포**이고 갱신 주기가
  연 1회입니다. 지원사업 공고는 공모전과 성격도 달라 별도 소스로 두는 게 맞다고 판단했습니다.
  (참고: `apis.data.go.kr/1421000/bizinfo/pblancList` 같은 경로는 현재 `NO_OPENAPI_SERVICE_ERROR`)

## 한계 (v1에서 의도적으로 뺀 것)

- 이벤터스·기업마당·K-Startup 등 나머지 소스 (위 §왜 없는지 참조)
- 자격 판정은 규칙 기반이다 — 시·군 단위 제한, 트랙별 예외, 서류 요건은 `unknown`으로 남는다
- 발표일은 상세 원문 문장에서 뽑은 **추정값**이다(ICS에 `[발표?]`). 상세를 한 번도 열지 않은 공고는 발표일이 없다
- 원격 호스팅/OAuth — 1인 로컬(stdio) 기준

## 개발

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest      # 66 tests, 네트워크 없이 파서·DB·ICS·자격·변경·브리핑 검증
```

## 라이선스

MIT. 수집 대상 사이트의 약관·robots.txt를 존중하며, 데이터의 권리는 각 주최·플랫폼에 있습니다.
