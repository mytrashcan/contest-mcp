"""제출한 공모전의 결과 발표 감시.

감시만 한다. **스케줄링은 밖에서 한다** — MCP 서버는 클라이언트가 띄운 stdio 프로세스라
세션이 끝나면 함께 종료된다. 서버 안에 스케줄러를 넣으면 "돌고 있다고 믿은 감시"가
조용히 죽는다. 그래서 여기에는 ``check()`` 한 번(=1회 점검)만 있고, 주기는 부르는 쪽
(Hermes cron·launchd·MCP 클라이언트)이 정한다.

소스 두 종류:

- **게시판**(``BOARD_SOURCES``): 목록 HTML의 ``<tr>`` 한 줄을 한 항목으로 보고, 칸 경계로
  쪼갠 뒤 가장 긴 조각을 제목으로 쓴다. KEIT(.es) · KSPO(.do) · K-Petro · 대구 DCNC(gnuboard)
  · 성평등 챌린지(JSP)가 이 구조라 파서 하나로 커버된다.
- **공고**(``DETAIL_SOURCES``): 공고 상세 파서(``detail.py``)의 ``date_mentions`` 를 감시한다.
  페이지 텍스트를 직접 긁으면 다른 공모전 배너의 발표일이 섞여 오탐이 난다.

JS로 목록을 그리는 사이트(ksponco·data1window·ex.co.kr)는 정적 HTML에 글이 없어 여기 넣지
않는다 — 개별통보·이메일로 오는 건 메일 쪽에서 잡는다.

상태 파일: ``CONTEST_MCP_WATCH`` 환경변수 또는 ``~/.contest-mcp/watch.json``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import detail, util
from .http import Fetcher

DEFAULT_WATCH = "~/.contest-mcp/watch.json"

# 결과 성격 글에 붙는 말. '발표'는 '발표심사'와 겹쳐서 일부러 넣지 않는다.
RESULT_WORDS = re.compile(r"결과|수상|수상자|선정|당첨|시상|입상")
DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
MAX_KEYS = 300

# 공고 상세에서 뽑은 일정 문구 중 '사이트 껍데기'(다른 공모전 목록·배너) 흔적을 걸러낸다.
# 껍데기 문구에는 D-day 배지·접수 상태·사이트 목록 표식이 붙는다. 본문/표의 일정에는 없다.
CHROME_HINT = re.compile(r"D-\d|접수중|접수예정|//-->|주최 \.|대상 \.|전체현황")


@dataclass(frozen=True)
class BoardSource:
    """감시할 게시판 하나. ``must`` 는 제목에 함께 있어야 하는 말(정규식)."""

    name: str
    url: str
    must: str | None = None


BOARD_SOURCES: tuple[BoardSource, ...] = (
    BoardSource(
        "대구 탄소중립센터",
        "https://www.dcnc.or.kr/bbs/board.php?bo_table=notice",
        r"대회|공모|AI|데이터",
    ),
    BoardSource("성평등 AI 챌린지", "https://mogefdatacontest.co.kr/notice.jsp"),
    BoardSource(
        "KEIT 아이디어 공모전",
        "https://www.keit.re.kr/menu.es?mid=a10606030000",
        r"아이디어|공모전|수상",
    ),
    # KSPO(kspo.or.kr) 공지사항은 robots.txt 가 `Disallow: /` 라 여기 넣지 않는다.
    # 공단 공모 결과는 개별 통보·이메일로 오고, 확정일은 캘린더에 따로 적는다.
    BoardSource(
        "K-Petro 공지사항",
        "https://www.kpetro.or.kr/lay1/program/S1T52C253/bbs/notice/All/list.do",
        r"아이디어|공모|수상|결과",
    ),
)

# 이름 -> 내 DB의 공모전 id(공고 일정 감시)
DETAIL_SOURCES: dict[str, str] = {
    "성평등 AI 챌린지(공고)": "contestkorea:202610300005",
    "사회보장 데이터 AI(공고)": "contestkorea:202610270001",
    "재정교육 아이디어(공고)": "allcon:544469",
}


def watch_path() -> Path:
    return Path(os.environ.get("CONTEST_MCP_WATCH", DEFAULT_WATCH)).expanduser()


# ------------------------------------------------------------------ HTML 파싱
def rows(html: str) -> list[str]:
    """목록 HTML → ``<tr>`` 한 줄씩. 칸 경계는 ``|`` 로 남긴다."""
    body = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
    out = []
    for raw in re.findall(r"<tr[\s\S]*?</tr>", body, re.I):
        cells = [util.clean(cell) for cell in re.split(r"</t[dh]>", raw, flags=re.I)]
        line = " | ".join(cell for cell in cells if cell)
        if 8 < len(line) < 300:
            out.append(line)
    return out


def title_of(row: str) -> str | None:
    """행에서 제목으로 볼 조각. 날짜·숫자만 있는 칸(번호·조회·작성일)은 버린다."""
    pieces = [p.strip() for p in row.split("|") if p.strip()]
    cand = [p for p in pieces if len(p) >= 6 and not p.isdigit() and not DATE_RE.fullmatch(p)]
    if not cand:
        return None
    return max(cand, key=len)


def date_of(row: str) -> str | None:
    match = DATE_RE.search(row)
    if not match:
        return None
    return f"{match.group(1)}.{int(match.group(2)):02d}.{int(match.group(3)):02d}"


def board_hits(html: str, source: BoardSource) -> dict[str, str]:
    """게시판 HTML → {제목: 날짜}. 결과 성격 + 소스별 필수어를 통과한 글만."""
    must = re.compile(source.must) if source.must else None
    hits: dict[str, str] = {}
    for row in rows(html):
        if not RESULT_WORDS.search(row):
            continue
        if must is not None and not must.search(row):
            continue
        title = title_of(row)
        if not title:
            continue
        hits[title] = date_of(row) or ""
    return hits


def detail_hits(fetcher: Fetcher, conn, ident: str) -> tuple[dict[str, str], str]:
    """공고 상세의 일정 문구 → ({'심사 2026-11-25': 근거}, 원문 URL)."""
    item = None
    if conn is not None:
        from . import db

        item = db.get(conn, ident)
    if not item or not item.get("url"):
        raise ValueError(f"공모전을 찾을 수 없음: {ident}")
    parsed = detail.fetch_and_parse(fetcher, item["url"])
    mentions: dict[str, str] = {}
    for mention in parsed.get("date_mentions") or []:
        full = mention.get("evidence") or ""
        # 자르기 전에 원문 전체로 판정한다 — 잘린 문구에는 껍데기 표식이 안 보일 수 있다.
        if CHROME_HINT.search(full):
            continue  # 다른 공모전 목록·배너에서 딸려온 일정
        key = f"{mention.get('label')} {mention.get('date')}"
        mentions[key] = full[:90]
    return mentions, item["url"]


# ------------------------------------------------------------------ 상태 파일
def load_state(path: Path | None = None) -> dict:
    path = path or watch_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "sources": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return {"version": 1, "sources": {}}
    return data


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or watch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _diff(state: dict, name: str, hits: dict[str, str], baseline: bool) -> tuple[list[str], bool]:
    """이번 관측과 저장된 키를 비교. (새 키 목록, 기준선 여부)."""
    entry = state["sources"].get(name)
    known = entry.get("keys") if isinstance(entry, dict) else None
    if baseline or not isinstance(known, list):
        state["sources"][name] = {"keys": list(hits)[:MAX_KEYS], "items": len(hits)}
        return [], True
    known_set = set(known)
    fresh = [key for key in hits if key not in known_set]
    merged = list(hits) + [key for key in known if key not in hits]
    state["sources"][name] = {"keys": merged[:MAX_KEYS], "items": len(hits)}
    return fresh, False


# ------------------------------------------------------------------ 1회 점검
def check(
    *,
    fetcher: Fetcher | None = None,
    conn=None,
    state_path: Path | None = None,
    only: list[str] | None = None,
    baseline: bool = False,
) -> dict:
    """한 번 점검하고 새 소식만 돌려준다. 상태 파일에 이번 관측을 남긴다.

    처음 보는 소스는 기준선만 저장하고 **알리지 않는다**(첫 실행 폭탄 방지).
    """
    fetcher = fetcher or Fetcher()
    path = state_path or watch_path()
    state = load_state(path)
    state.setdefault("sources", {})
    wanted = {name.strip() for name in only} if only else None

    report: dict = {"new": [], "sources": {}, "failures": [], "baselined": [], "warnings": []}

    for source in BOARD_SOURCES:
        if wanted and source.name not in wanted:
            continue
        try:
            html = fetcher.get(source.url)
        except Exception as exc:  # robots 거부·타임아웃·차단
            report["sources"][source.name] = {"checked": False, "error": str(exc)}
            report["failures"].append(f"{source.name}({type(exc).__name__})")
            continue
        hits = board_hits(html, source)
        fresh, was_baseline = _diff(state, source.name, hits, baseline)
        if was_baseline:
            report["baselined"].append(source.name)
        report["sources"][source.name] = {
            "checked": True,
            "items": len(hits),
            "new": len(fresh),
            "baselined": was_baseline,
        }
        for key in fresh:
            report["new"].append(
                {
                    "source": source.name,
                    "kind": "board",
                    "title": key,
                    "date": hits.get(key) or None,
                    "url": source.url,
                }
            )

    for name, ident in DETAIL_SOURCES.items():
        if wanted and name not in wanted:
            continue
        try:
            hits, url = detail_hits(fetcher, conn, ident)
        except Exception as exc:
            report["sources"][name] = {"checked": False, "error": str(exc)}
            report["failures"].append(f"{name}({type(exc).__name__})")
            continue
        fresh, was_baseline = _diff(state, name, hits, baseline)
        if was_baseline:
            report["baselined"].append(name)
        report["sources"][name] = {
            "checked": True,
            "items": len(hits),
            "new": len(fresh),
            "baselined": was_baseline,
        }
        for key in fresh:
            report["new"].append(
                {
                    "source": name,
                    "kind": "schedule",
                    "title": key,
                    "date": None,
                    "evidence": hits.get(key) or None,
                    "url": url,
                }
            )

    state["last_run"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    state["last_failures"] = report["failures"]
    save_state(state, path)
    report["warnings"].extend(fetcher.warnings)
    total = len(BOARD_SOURCES) + len(DETAIL_SOURCES)
    report["checked"] = len(report["sources"])
    report["all_failed"] = bool(report["failures"]) and len(report["failures"]) >= total
    return report
