"""수집 소스 레지스트리."""

from __future__ import annotations

from . import allcon, contestkorea, linkareer, thinkgood, wevity

MODULES = (wevity, thinkgood, allcon, linkareer, contestkorea)

SOURCES = {module.NAME: module for module in MODULES}

__all__ = ["MODULES", "SOURCES", "allcon", "contestkorea", "linkareer", "thinkgood", "wevity"]
