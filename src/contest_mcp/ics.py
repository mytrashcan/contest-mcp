"""ICS 일정 파일 생성 (종일 이벤트 + 사전 알림).

날짜가 없는 기한은 이벤트로 만들지 않는다. 손으로 날짜를 지어내면 캘린더가 거짓말을 한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path


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
    now = (stamp or dt.date.today()).strftime("%Y%m%dT%H%M%SZ")
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
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_escape(event.get('uid') or f'{date}-{event.get('summary', '')}')}@contest-mcp",
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
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build(events, alarm_days=alarm_days).encode("utf-8"))
    return target
