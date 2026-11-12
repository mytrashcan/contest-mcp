"""프로필 파일(자격 판정 입력) 동작 검증."""

from __future__ import annotations

import json
from pathlib import Path

from contest_mcp import profile

EXAMPLE = Path(__file__).resolve().parents[1] / "profile.example.json"


def test_example_profile_is_valid_json_with_required_keys():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    for key in ("school", "region", "status", "team_available"):
        assert key in data, key
    assert isinstance(data["age"], int)
    assert isinstance(data["team_available"], bool)


def test_load_reads_env_path(tmp_path, monkeypatch):
    target = tmp_path / "profile.json"
    target.write_text(json.dumps({"school": "경북대학교", "region": "대구"}), encoding="utf-8")
    monkeypatch.setenv("CONTEST_MCP_PROFILE", str(target))
    loaded = profile.load()
    assert loaded["school"] == "경북대학교"
    assert loaded["region"] == "대구"


def test_missing_profile_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_MCP_PROFILE", str(tmp_path / "nope.json"))
    assert profile.load() == {}


def test_broken_profile_is_ignored(tmp_path, monkeypatch):
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CONTEST_MCP_PROFILE", str(target))
    assert profile.load() == {}


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEST_MCP_PROFILE", str(tmp_path / "p.json"))
    profile.save({"school": "경북대학교", "region": "경북"})
    assert profile.load()["region"] == "경북"
