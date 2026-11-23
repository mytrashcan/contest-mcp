"""범용 감시: 새 공모전 발견 · 내 공고 일정 변경 · 임의 페이지의 결과 발표.

감시 대상은 코드에 박지 않고 **데이터로 등록**한다(``~/.contest-mcp/watch.json``).
공모전은 어디서든 열리므로 "사이트 목록"을 코드에 두면 금방 낡는다. 대신 대상 3종류를 둔다.

- ``url``: 임의 페이지. 텍스트를 줄 단위로 뜯어 **지난 점검에 없던 줄** 중 매칭되는 것만 알린다.
  (페이지 전체 해시 비교는 광고·조회수만 바뀌어도 울려서 쓰지 않는다)
- ``contest``: 내 DB의 공모전. 상세 파서가 뽑은 일정 문구를 감시한다(사이트 구조와 무관).
- ``query``: 검색어. ``refresh`` 뒤에 조건에 맞는 새 공고가 들어오면 알린다.
  소스가 늘어나도 코드 수정 없이 따라간다.

**스케줄러는 없다.** MCP 서버는 클라이언트가 띄운 stdio 프로세스라 세션이 끝나면 함께 죽는다.
주기는 부르는 쪽(에이전트 cron·launchd)이 정하고, 여기서는 1회 점검만 한다.

상태 파일: ``CONTEST_MCP_WATCH`` 환경변수 또는 ``~/.contest-mcp/watch.json``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Literal

from . import detail, util
from .http import Fetcher

DEFAULT_WATCH = "~/.contest-mcp/watch.json"

Kind = Literal["url", "contest", "query"]

# 기본 매칭: 결과 성격 글. '발표'는 '발표심사'와 겹쳐서 일부러 넣지 않는다.
RESULT_WORDS = r"결과|수상|수상자|선정|당첨|시상|입상"
DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
# 공고 상세에서 뽑은 문구 중 '사이트 껍데기'(다른 공모전 목록·배너) 흔적.
CHROME_HINT = re.compile(r"D-\d|접수중|접수예정|//-->|주최 \.|대상 \.|전체현황")
MAX_KEYS = 300
MAX_LINE = 200


def watch_path() -> Path:
    return Path(os.environ.get("CONTEST_MCP_WATCH", DEFAULT_WATCH)).expanduser()


def _empty_state() -> dict:
    return {"version": 2, "targets": [], "last_run": None, "last_failures": []}


# ------------------------------------------------------------------ 레지스트리
def load_state(path: Path | None = None) -> dict:
    path = path or watch_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        return _empty_state()
    return data


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or watch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def target_id(kind: Kind, value: str) -> str:
    return f"{kind}:{value}"


def add_target(
    state: dict,
    kind: Kind,
    value: str,
    *,
    match: str | None = None,
    label: str | None = None,
) -> dict:
    """감시 대상 등록. 같은 대상이 이미 있으면 설정만 갱신한다(기존 관측은 유지)."""
    ident = target_id(kind, value)
    for target in state["targets"]:
        if target.get("id") == ident:
            if match:
                target["match"] = match
            if label:
                target["label"] = label
            return target
    target: dict = {"id": ident, "kind": kind, "value": value}
    if kind == "url" and match:
        target["match"] = match
    if label:
        target["label"] = label
    state["targets"].append(target)
    return target


def remove_target(state: dict, ident: str) -> bool:
    """id(``url:...``) 또는 목록 번호(1부터)로 삭제."""
    for index, target in enumerate(state["targets"]):
        if target.get("id") == ident or str(index + 1) == ident:
            state["targets"].pop(index)
            return True
    return False


def list_targets(state: dict) -> list[dict]:
    """사람이 읽기 좋은 형태의 대상 목록."""
    out = []
    for index, target in enumerate(state["targets"], start=1):
        out.append(
            {
                "index": index,
                "id": target.get("id"),
                "kind": target.get("kind"),
                "value": target.get("value"),
                "label": target.get("label"),
                "match": target.get("match"),
                "keys": len(target.get("keys") or []),
            }
        )
    return out


# ------------------------------------------------------------------ HTML 파싱
def lines_of(html: str) -> list[str]:
    """임의 페이지 → 사람이 읽는 한 줄들. 어느 사이트든 같은 규칙으로 뜯는다."""
    body = re.sub(r"<(script|style|noscript)[\s\S]*?</\1>", " ", html, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"</(p|div|li|tr|td|th|h[1-6]|section|article|dd|dt)>", "\n", body, flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    for chunk in body.split("\n"):
        line = util.clean(chunk)
        if not (6 <= len(line) <= MAX_LINE) or line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out


def date_of(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    return f"{match.group(1)}.{int(match.group(2)):02d}.{int(match.group(3)):02d}"


def url_hits(html: str, match: str | None) -> dict[str, dict]:
    """임의 페이지 → {줄: 항목}. 매칭되는 줄만 남긴다."""
    pattern = re.compile(match or RESULT_WORDS)
    hits: dict[str, dict] = {}
    for line in lines_of(html):
        if not pattern.search(line):
            continue
        hits[line] = {"title": line, "date": date_of(line)}
    return hits


def detail_hits(fetcher: Fetcher, conn, ident: str) -> tuple[dict[str, dict], str]:
    """공모전 상세의 일정 문구 → ({'심사 2026-11-25': 항목}, 원문 URL)."""
    item = None
    if conn is not None:
        from . import db

        item = db.get(conn, ident)
    if not item or not item.get("url"):
        raise ValueError(f"공모전을 찾을 수 없음(또는 URL 없음): {ident}")
    parsed = detail.fetch_and_parse(fetcher, item["url"])
    hits: dict[str, dict] = {}
    for mention in parsed.get("date_mentions") or []:
        evidence = mention.get("evidence") or ""
        # 자르기 전에 원문 전체로 판정한다 — 잘린 문구에는 껍데기 표식이 안 보일 수 있다.
        if CHROME_HINT.search(evidence):
            continue  # 다른 공모전 목록·배너에서 딸려온 일정
        key = f"{mention.get('label')} {mention.get('date')}"
        hits[key] = {"title": key, "evidence": evidence[:90], "url": item["url"], "date": None}
    return hits, item["url"]


def query_hits(conn, query: str) -> dict[str, dict]:
    """검색어에 걸리는 공고 id 집합. '새 공고 발견' 감시용."""
    if conn is None:
        raise RuntimeError("검색 감시는 DB 연결이 필요합니다")
    from . import db

    rows = db.search(conn, query=query, status="all", dday_max=None, limit=300)
    hits: dict[str, dict] = {}
    for row in rows:
        hits[row["id"]] = {
            "title": row.get("name") or row["id"],
            "url": row.get("url"),
            "date": row.get("apply_end"),
        }
    return hits


# ------------------------------------------------------------------ 1회 점검
def _diff(target: dict, hits: dict[str, dict], baseline: bool) -> list[dict]:
    """이번 관측과 저장된 키를 비교해 **새 항목**만 돌려주고 대상 상태를 갱신한다."""
    known = target.get("keys")
    if baseline or not isinstance(known, list):
        target["keys"] = list(hits)[:MAX_KEYS]
        target["baselined"] = True
        return []
    known_set = set(known)
    fresh = [key for key in hits if key not in known_set]
    target["keys"] = (list(hits) + [key for key in known if key not in hits])[:MAX_KEYS]
    target.pop("baselined", None)
    return [
        {
            **hits[key],
            "target": target.get("id"),
            "kind": target.get("kind"),
            "label": target.get("label"),
        }
        for key in fresh
    ]


def check(
    *,
    fetcher: Fetcher | None = None,
    conn=None,
    state_path: Path | None = None,
    baseline: bool = False,
    only: list[str] | None = None,
) -> dict:
    """등록된 대상을 한 번 점검한다. 첫 관측은 기준선만 저장하고 알리지 않는다."""
    fetcher = fetcher or Fetcher()
    path = state_path or watch_path()
    state = load_state(path)
    wanted = {name.strip() for name in only} if only else None

    report: dict = {
        "new": [],
        "targets": [],
        "failures": [],
        "baselined": [],
        "checked": 0,
        "all_failed": False,
    }
    if not state["targets"]:
        report["note"] = (
            "등록된 감시 대상이 없습니다 — `watch --add-url URL` · `--add-contest ID` · "
            "`--add-query 검색어` 로 추가하세요."
        )
        return report

    for target in state["targets"]:
        ident = str(target.get("id"))
        if wanted and ident not in wanted:
            continue
        kind = target.get("kind")
        report["checked"] += 1
        try:
            if kind == "url":
                hits = url_hits(fetcher.get(str(target["value"])), target.get("match"))
            elif kind == "contest":
                hits, _ = detail_hits(fetcher, conn, str(target["value"]))
            elif kind == "query":
                hits = query_hits(conn, str(target["value"]))
            else:
                raise ValueError(f"알 수 없는 감시 종류: {kind}")
        except Exception as exc:
            report["targets"].append({"id": ident, "checked": False, "error": str(exc)})
            report["failures"].append(f"{ident}({type(exc).__name__})")
            continue
        fresh = _diff(target, hits, baseline)
        if target.pop("baselined", False):
            report["baselined"].append(ident)
        report["targets"].append(
            {"id": ident, "checked": True, "items": len(hits), "new": len(fresh)}
        )
        report["new"].extend(fresh)

    state["last_run"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    state["last_failures"] = report["failures"]
    save_state(state, path)
    report["all_failed"] = bool(report["failures"]) and len(report["failures"]) >= report["checked"]
    report["warnings"] = list(fetcher.warnings)
    return report
