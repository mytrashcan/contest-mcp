"""HTTP 클라이언트: robots.txt 확인, 호스트별 요청 간격, 인증서 예외 호스트.

수집 예절을 코드에 고정해 둔다.
- 요청 전에 robots.txt를 확인하고 Disallow면 소스를 건너뛴다(결과에 이유를 남긴다).
- 같은 호스트에는 최소 ``MIN_INTERVAL`` 초 간격을 둔다.
- 인증서 체인이 불완전한 사이트는 ``insecure_hosts`` 에 명시한 경우에만 검증을 끄고,
  그 사실을 ``warnings`` 에 남긴다(조용히 무시하지 않는다).
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 contest-mcp/0.1 (+https://github.com/mytrashcan/contest-mcp)"
)

DEFAULT_INSECURE_HOSTS = frozenset({"www.all-con.co.kr"})


class FetchError(RuntimeError):
    """네트워크·HTTP 오류."""


class RobotsDenied(RuntimeError):
    """robots.txt가 수집을 막은 경우."""


class Fetcher:
    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        timeout: float = 20.0,
        insecure_hosts: frozenset[str] = DEFAULT_INSECURE_HOSTS,
        sleep=time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.insecure_hosts = insecure_hosts
        self.sleep = sleep
        self.warnings: list[str] = []
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, list[str] | None] = {}

    # ---------------------------------------------------------------- robots
    def _robots_rules(self, host: str) -> list[str] | None:
        if host in self._robots:
            return self._robots[host]
        url = f"https://{host}/robots.txt"
        try:
            body = self._request(url, headers={})
        except Exception:
            # robots.txt를 못 받으면 막지 않는다(없는 경우와 구분 불가). 판단 불가는 allow.
            self._robots[host] = None
            return None
        rules: list[str] = []
        applies = False
        for line in body.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                applies = value == "*"
            elif key == "disallow" and applies and value:
                rules.append(value)
        self._robots[host] = rules
        return rules

    def robots_allowed(self, url: str) -> tuple[bool, str]:
        parsed = urllib.parse.urlsplit(url)
        rules = self._robots_rules(parsed.netloc)
        if not rules:
            return (True, "")
        path = parsed.path or "/"
        for rule in rules:
            prefix = rule.rstrip("*")
            if path.startswith(prefix):
                return (False, f"robots.txt Disallow: {rule}")
        return (True, "")

    # ---------------------------------------------------------------- request
    def _wait(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            delay = self.min_interval - (time.monotonic() - last)
            if delay > 0:
                self.sleep(delay)
        self._last_hit[host] = time.monotonic()

    def _context(self, host: str) -> ssl.SSLContext | None:
        if host in self.insecure_hosts:
            note = f"{host}: 인증서 체인 불완전 → 검증 없이 수집"
            if note not in self.warnings:
                self.warnings.append(note)
            return ssl._create_unverified_context()  # noqa: SLF001 - 의도적 예외
        return None

    def _request(self, url: str, *, headers: dict[str, str], data: bytes | None = None) -> str:
        parsed = urllib.parse.urlsplit(url)
        self._wait(parsed.netloc)
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self._context(parsed.netloc)) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code} {url}") from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"{exc.reason} {url}") from exc
        except TimeoutError as exc:
            raise FetchError(f"timeout {url}") from exc
        return raw.decode("utf-8", "replace")

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        return self._request(url, headers=headers or {})

    def post_form(self, url: str, form: dict[str, object], headers: dict[str, str] | None = None) -> str:
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        body = urllib.parse.urlencode(form).encode()
        merged = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        return self._request(url, headers=merged, data=body)

    def post_json(self, url: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> str:
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        body = json.dumps(payload, ensure_ascii=False).encode()
        merged = {"Content-Type": "application/json; charset=UTF-8", **(headers or {})}
        return self._request(url, headers=merged, data=body)
