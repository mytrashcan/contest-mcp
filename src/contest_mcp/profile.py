"""사용자 프로필(자격 판정 입력). 없으면 판정은 unknown 으로 남는다.

경로: ``CONTEST_MCP_PROFILE`` 환경변수 또는 ``~/.contest-mcp/profile.json``.
저장소에 절대 커밋하지 않는다(개인정보).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = "~/.contest-mcp/profile.json"

FIELDS = ("school", "region", "status", "age", "team_available")

TEMPLATE = {
    "school": "경북대학교",
    "region": "대구",
    "status": "학부생",
    "age": None,
    "team_available": True,
    "note": "region은 주민등록지 기준. 학교 소재지로 갈음되지 않는다(도 단위 자격은 '경북 도민' 등).",
}


def path() -> Path:
    return Path(os.environ.get("CONTEST_MCP_PROFILE", DEFAULT_PATH)).expanduser()


def load() -> dict:
    """프로필 로드. 파일이 없으면 빈 dict (판정은 unknown으로 남는다)."""
    target = path()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(profile: dict) -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
