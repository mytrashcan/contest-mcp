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
| `search_contests` | 마감·분야·지역·검색어로 조회. 같은 공고가 여러 소스에 있으면 1건으로 묶고 출처를 함께 반환 |
| `get_contest` | 상세 파싱: 자격·시상·배점·제출물·발표·문의 + 근거 문장 + 발표일 언급 |
| `refresh` | 소스에서 목록 재수집 (`wevity`, `thinkgood`, `allcon`) |
| `deadlines` | 마감 일정 모으기, 옵션으로 `.ics` 캘린더 생성 |
| `mark` / `my_list` | 제출·폐기·관심없음 기록과 조회 |

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
python -m contest_mcp.cli detail wevity:112316        # 상세 원문 파싱
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
- **중복 병합**: 이름을 정규화(연도·괄호·기호 제거)한 해시로 묶습니다. 이름이 크게 다른 같은 공고
  (예: 주최명이 제목 앞에 붙은 경우)는 별개로 남습니다 — 오탐 병합보다 누락이 낫다는 판단입니다.
- **발표일**: 상세 원문에 문장으로만 존재하므로 추론하지 않고, 날짜 **언급**과 문맥을 그대로 반환합니다.

## 한계 (v1에서 의도적으로 뺀 것)

- 씽굿·올콘 외 소스(링커리어, 콘테스트코리아, 이벤터스, 기업마당, K-Startup) 미지원
- 자격 문단의 자동 판정(예: "경북대 재학생인 내가 낼 수 있나") — 원문 문장만 제공
- 발표일 자동 추출·캘린더 반영(위 `date_mentions`를 사람이 확인)
- 원격 호스팅/OAuth — 1인 로컬(stdio) 기준

## 개발

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest      # 26 tests, 네트워크 없이 파서·DB·ICS 검증
```

## 라이선스

MIT. 수집 대상 사이트의 약관·robots.txt를 존중하며, 데이터의 권리는 각 주최·플랫폼에 있습니다.
