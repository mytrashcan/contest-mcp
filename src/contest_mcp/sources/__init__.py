"""수집 소스 레지스트리."""

from __future__ import annotations

from . import allcon, thinkgood, wevity

SOURCES = {
    wevity.NAME: wevity,
    thinkgood.NAME: thinkgood,
    allcon.NAME: allcon,
}

__all__ = ["SOURCES", "allcon", "thinkgood", "wevity"]
