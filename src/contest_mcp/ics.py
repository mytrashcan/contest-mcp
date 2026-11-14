"""ICS 일정 파일 생성 (종일 이벤트 + 사전 알림).

날짜가 없는 기한은 이벤트로 만들지 않는다. 손으로 날짜를 지어내면 캘린더가 거짓말을 한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import util

ICS_MAGIC = b"BEGIN:VCALENDAR"


class UnsafePath(ValueError):
    """ICS를 쓰기에 안전하지 않은 경로."""


def safe_target(path: str | Path) -> Path:
    """쓸 경로를 검증한다. 경로는 모델이 넘기는 값이라 임의 파일을 덮어쓰지 못하게 막는다.

    - 확장자는 ``.ics`` 여야 한다.
    - 이미 있는 파일은 ICS일 때만 덮어쓴다.
    """
    target = Path(path).expanduser()
    if target.suffix.lower() != ".ics":
        raise UnsafePath(f"ICS 경로는 .ics 로 끝나야 합니다: {target}")
    if target.exists():
        if not target.is_file():
            raise UnsafePath(f"파일이 아닌 경로입니다: {target}")
        with target.open("rb") as handle:
            head = handle.read(len(ICS_MAGIC))
        if head != ICS_MAGIC:
            raise UnsafePath(f"ICS가 아닌 기존 파일은 덮어쓰지 않습니다: {target}")
    return target


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """75 옥텟에서 접는 RFC 5545 규칙."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: list[str] = []
    current = b""
    for char in line:
        piece = char.encode("utf-8")
        if len(current) + len(piece) > 73:
            chunks.append(current.decode("utf-8"))
            current = b""
        current += piece
    if current:
        chunks.append(current.decode("utf-8"))
    return "\r\n ".join(chunks)


def build(events: list[dict], *, alarm_days: int = 2, stamp: dt.date | None = None) -> str:
    """events: [{date: 'YYYY-MM-DD', summary, description?, url?}]. 반환값은 CRLF ICS 문자열."""
    now = (stamp or util.today()).strftime("%Y%m%dT%H%M%SZ")  # PC 로컬 날짜가 아니라 서울 기준
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//contest-mcp//KO//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        date = event.get("date")
        if not date:
            continue
        try:
            start = dt.date.fromisoformat(date)
        except ValueError:
            continue
        description = event.get("description") or ""
        if event.get("url"):
            description = f"{description}\n{event['url']}".strip()
        summary = event.get("summary") or ""
        uid_seed = event.get("uid") or f"{date}-{summary}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_escape(uid_seed)}@contest-mcp",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(start + dt.timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(event.get('summary') or '공모전')}",
        ]
        if description:
            lines.append(f"DESCRIPTION:{_escape(description)}")
        if alarm_days > 0:
            lines += [
                "BEGIN:VALARM",
                f"TRIGGER:-P{alarm_days}D",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escape(event.get('summary') or '공모전 마감')}",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def write(events: list[dict], path: str | Path, *, alarm_days: int = 2) -> Path:
    target = safe_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build(events, alarm_days=alarm_days).encode("utf-8"))
    return target
