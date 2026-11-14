"""HTTP 클라이언트: robots.txt 확인, 호스트별 요청 간격, 인증서 예외 호스트.

수집 예절을 코드에 고정해 둔다.
- 요청 전에 robots.txt를 확인하고 Disallow면 소스를 건너뛴다(결과에 이유를 남긴다).
- 같은 호스트에는 최소 ``MIN_INTERVAL`` 초 간격을 둔다.
- 인증서 체인이 불완전한 사이트는 ``insecure_hosts`` 에 명시한 경우에만 검증을 끄고,
  그 사실을 ``warnings`` 에 남긴다(조용히 무시하지 않는다).
- http/https 가 아닌 주소(``file://`` 등)는 받지 않고, 응답 크기에 상한을 둔다.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 contest-mcp/0.1 (+https://github.com/mytrashcan/contest-mcp)"
)
ROBOTS_AGENT = "contest-mcp"  # robots.txt에서 우리를 가리키는 그룹 이름

DEFAULT_INSECURE_HOSTS = frozenset({"www.all-con.co.kr"})
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
ALLOWED_SCHEMES = frozenset({"http", "https"})

Rule = tuple[bool, str]  # (allow 여부, 경로 패턴)


class FetchError(RuntimeError):
    """네트워크·HTTP 오류."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RobotsDenied(RuntimeError):
    """robots.txt가 수집을 막은 경우."""


def parse_robots(body: str, agent: str = ROBOTS_AGENT) -> list[Rule]:
    """robots.txt 본문 → 이 수집기에 적용되는 규칙 목록.

    연속된 ``User-agent`` 줄은 한 그룹이다. 우리 이름이 들어간 그룹이 있으면 그것만,
    없으면 ``*`` 그룹을 쓴다. 값이 빈 ``Disallow:`` 는 "막지 않음"이라 규칙으로 치지 않는다.
    """
    groups: list[tuple[list[str], list[Rule]]] = []
    agents: list[str] = []
    rules: list[Rule] = []
    in_rules = False
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if in_rules:
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif key == "sitemap":
            continue  # 그룹에 속하지 않는 줄
        else:
            in_rules = True
            if key in {"allow", "disallow"} and agents and value:
                rules.append((key == "allow", value))
    if agents:
        groups.append((agents, rules))

    mine = agent.lower()

    def names_me(names: list[str]) -> bool:
        return any(name and name != "*" and name in mine for name in names)

    own = [group_rules for names, group_rules in groups if names_me(names)]
    chosen = own or [group_rules for names, group_rules in groups if "*" in names]
    return [rule for group_rules in chosen for rule in group_rules]


def _robots_regex(pattern: str) -> re.Pattern[str]:
    """robots 패턴(``*`` 와일드카드, 끝의 ``$`` 고정)을 정규식으로."""
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in core.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


def robots_verdict(rules: list[Rule], target: str) -> tuple[bool, str]:
    """경로(+쿼리)에 가장 길게 맞는 규칙이 이긴다. 길이가 같으면 Allow가 이긴다."""
    best: tuple[int, bool, str] | None = None
    for allow, pattern in rules:
        if not _robots_regex(pattern).match(target):
            continue
        if best is None or (len(pattern), allow) > (best[0], best[1]):
            best = (len(pattern), allow, pattern)
    if best is None or best[1]:
        return (True, "")
    return (False, f"robots.txt Disallow: {best[2]}")


class Fetcher:
    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        timeout: float = 20.0,
        insecure_hosts: frozenset[str] = DEFAULT_INSECURE_HOSTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        sleep=time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.insecure_hosts = insecure_hosts
        self.max_bytes = max_bytes
        self.sleep = sleep
        self.warnings: list[str] = []
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, list[Rule] | None] = {}

    def _warn(self, note: str) -> None:
        if note not in self.warnings:
            self.warnings.append(note)

    # ---------------------------------------------------------------- robots
    def _robots_rules(self, scheme: str, host: str) -> list[Rule] | None:
        if host in self._robots:
            return self._robots[host]
        url = f"{scheme}://{host}/robots.txt"
        try:
            body = self._raw(url, headers={}).decode("utf-8", "replace")
        except Exception as exc:
            # 4xx는 "robots.txt 없음"이라 막지 않는다. 그 밖의 실패(5xx·타임아웃)도 수집은 계속하되,
            # 확인하지 못했다는 사실을 조용히 넘기지 않고 경고로 남긴다.
            status = getattr(exc, "status", None)
            if not (isinstance(status, int) and 400 <= status < 500):
                self._warn(f"{host}: robots.txt 확인 실패({exc}) → 확인 없이 수집")
            self._robots[host] = None
            return None
        rules = parse_robots(body)
        self._robots[host] = rules
        return rules

    def robots_allowed(self, url: str) -> tuple[bool, str]:
        parsed = urllib.parse.urlsplit(url)
        self._check_scheme(parsed, url)
        rules = self._robots_rules(parsed.scheme, parsed.netloc)
        if not rules:
            return (True, "")
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        return robots_verdict(rules, target)

    # ---------------------------------------------------------------- request
    @staticmethod
    def _check_scheme(parsed: urllib.parse.SplitResult, url: str) -> None:
        if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.netloc:
            raise FetchError(f"허용하지 않는 주소(http/https만 가능): {url}")

    def _wait(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            delay = self.min_interval - (time.monotonic() - last)
            if delay > 0:
                self.sleep(delay)
        self._last_hit[host] = time.monotonic()

    def _context(self, host: str) -> ssl.SSLContext | None:
        if host in self.insecure_hosts:
            self._warn(f"{host}: 인증서 체인 불완전 → 검증 없이 수집")
            return ssl._create_unverified_context()  # noqa: SLF001 - 의도적 예외
        return None

    def _raw(self, url: str, *, headers: dict[str, str], data: bytes | None = None) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        self._check_scheme(parsed, url)
        self._wait(parsed.netloc)
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **headers})
        try:
            context = self._context(parsed.netloc)
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                body = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code} {url}", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"{exc.reason} {url}") from exc
        except TimeoutError as exc:
            raise FetchError(f"timeout {url}") from exc
        if len(body) > self.max_bytes:
            raise FetchError(f"응답이 너무 큼(>{self.max_bytes // (1024 * 1024)}MB) {url}")
        return body

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        return self._raw(url, headers=headers or {}).decode("utf-8", "replace")

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """첨부파일(hwp·pdf 등)처럼 텍스트 디코드 없이 바이트로 받아야 할 때."""
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        return self._raw(url, headers=headers or {})

    def post_form(self, url: str, form: dict[str, object], headers: dict[str, str] | None = None) -> str:
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        body = urllib.parse.urlencode(form).encode()
        merged = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        return self._raw(url, headers=merged, data=body).decode("utf-8", "replace")

    def post_json(self, url: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> str:
        ok, reason = self.robots_allowed(url)
        if not ok:
            raise RobotsDenied(reason)
        body = json.dumps(payload, ensure_ascii=False).encode()
        merged = {"Content-Type": "application/json; charset=UTF-8", **(headers or {})}
        return self._raw(url, headers=merged, data=body).decode("utf-8", "replace")
