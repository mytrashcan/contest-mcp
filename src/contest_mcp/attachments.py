"""공고 첨부파일(hwpx·docx·pdf·zip·hwp) 텍스트 추출.

배점·제출물·발표 방식은 본문에 없고 첨부에만 있는 경우가 많다(실측: 배점 2/12).
표준 라이브러리로 처리 가능한 형식(hwpx·docx=zip+xml, zip=묶음)은 바로 파싱하고,
pdf는 `pdftotext`, hwp(5.x)는 `hwp5txt`/LibreOffice가 있을 때만 시도한다.
없으면 **조용히 넘어가지 않고 이유를 남긴다**.

실측 주의: 씽굿 상세의 첨부는 `href="javascript:void(0)"` + JS 다운로드라 자동 수집이 불가하고,
콘테스트코리아는 `file_dn.php?...` 링크에 파일명이 앵커 텍스트로만 온다 → 링크가 아니라 **앵커 텍스트의
확장자**로도 판별한다.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

EXTS = (".hwpx", ".docx", ".pdf", ".hwp", ".zip")
# 압축을 푼 뒤 크기 상한. 작은 zip이 거대한 파일로 풀리는 경우(zip bomb)를 막는다.
MAX_MEMBER_BYTES = 20 * 1024 * 1024
_EXT_RE = re.compile(r"\.(hwpx|docx|pdf|hwp|zip)(?=$|[?#\s\"'<>])", re.I)
_ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _ext_of(value: str) -> str | None:
    match = _EXT_RE.search(value or "")
    return f".{match.group(1).lower()}" if match else None


def extract_links(html: str, base_url: str = "") -> list[dict]:
    """상세 HTML의 첨부 링크. href 또는 앵커 텍스트의 확장자로 판별한다."""
    found: list[dict] = []
    seen: set[str] = set()
    for href, inner in _ANCHOR.findall(html or ""):
        label = " ".join(_TAG.sub(" ", inner).split())
        ext = _ext_of(href) or _ext_of(label)
        if not ext:
            continue
        if href.lower().startswith("javascript") or href.startswith("#"):
            continue  # JS 다운로드(씽굿 등)는 자동 수집 불가
        url = urljoin(base_url, href) if base_url else href
        scheme = urlsplit(url).scheme.lower()
        if scheme and scheme not in {"http", "https"}:
            continue  # file:// 등 웹 주소가 아닌 링크는 받지 않는다
        if url in seen:
            continue
        seen.add(url)
        name = label if label and _ext_of(label) else url.rsplit("/", 1)[-1].split("?")[0]
        found.append({"url": url, "name": name, "ext": ext})
    return found


def _xml_text(xml: str) -> str:
    """hwpx/docx 문단 태그를 살려 평문으로(문단 경계는 줄바꿈 유지)."""
    text = xml.replace("</hp:p>", "\n").replace("</w:p>", "\n")
    text = _TAG.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    """zip 안의 파일 1개를 읽되, 풀린 크기가 상한을 넘으면 읽지 않는다."""
    if archive.getinfo(name).file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"압축 해제 크기 초과(>{MAX_MEMBER_BYTES // (1024 * 1024)}MB): {name}")
    return archive.read(name)


def hwpx_text(data: bytes) -> str:
    """hwpx = zip + Contents/section*.xml (표준 라이브러리만)."""
    chunks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = sorted(n for n in archive.namelist() if re.match(r"Contents/section\d+\.xml$", n))
        for name in names:
            chunks.append(_xml_text(_read_member(archive, name).decode("utf-8", "replace")))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return _xml_text(_read_member(archive, "word/document.xml").decode("utf-8", "replace"))


def _run(command: list[str], suffix: str, data: bytes, *, trailing: tuple[str, ...] = ()) -> tuple[str, str]:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        completed = subprocess.run(
            [*command, str(path), *trailing],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return completed.stdout, completed.stderr
    finally:
        path.unlink(missing_ok=True)


def _pdf_text(data: bytes) -> tuple[str, str | None]:
    if not shutil.which("pdftotext"):
        return "", "pdftotext 없음 (poppler 미설치)"
    # pdftotext는 출력 경로를 생략하면 '<입력>.txt' 파일로 쓴다 → stdout은 '-'를 명시해야 한다.
    out, _err = _run(["pdftotext", "-layout"], ".pdf", data, trailing=("-",))
    return out, None


def _hwp_text(data: bytes) -> tuple[str, str | None]:
    if shutil.which("hwp5txt"):
        out, _err = _run(["hwp5txt"], ".hwp", data)
        return out, None
    if shutil.which("soffice"):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.hwp"
            source.write_bytes(data)
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "txt", "--outdir", tmp, str(source)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            converted = Path(tmp) / "a.txt"
            if converted.exists():
                return converted.read_text(encoding="utf-8", errors="replace"), None
            return "", "soffice 변환 실패"
    return "", "hwp 파서 없음 (hwp5txt·soffice 미설치)"


def _extract(ext: str, data: bytes) -> tuple[str, str | None, str | None]:
    """(텍스트, 사용한 방법, 오류) — 지원 형식 분기."""
    if ext == ".hwpx":
        return hwpx_text(data), "zip+xml", None
    if ext == ".docx":
        return docx_text(data), "zip+xml", None
    if ext == ".pdf":
        text, error = _pdf_text(data)
        return text, ("pdftotext" if text else None), error
    if ext == ".hwp":
        text, error = _hwp_text(data)
        return text, ("hwp" if text else None), error
    if ext == ".zip":
        texts: list[str] = []
        methods: list[str] = []
        errors: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                inner_ext = _ext_of(name)
                if inner_ext not in {".hwpx", ".docx", ".pdf"}:
                    continue
                try:
                    payload = _read_member(archive, name)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                inner_text, inner_method, inner_error = _extract(inner_ext, payload)
                if inner_text:
                    texts.append(inner_text)
                    if inner_method:
                        methods.append(f"zip>{inner_method}")
                elif inner_error:
                    errors.append(f"{name}: {inner_error}")
        method = ",".join(sorted(set(methods))) or None
        error = "; ".join(errors) or None
        if not texts and not error:
            error = "zip 안에 지원 형식(hwpx·docx·pdf) 없음"
        return "\n".join(texts), method, error
    return "", None, f"지원하지 않는 형식: {ext}"


def text_of(fetcher, link: dict) -> dict:
    """첨부 1건 → 텍스트. 실패해도 예외를 올리지 않고 이유를 담는다."""
    result = {
        "url": link["url"],
        "name": link.get("name"),
        "ext": link.get("ext"),
        "method": None,
        "chars": 0,
        "text": "",
        "error": None,
    }
    try:
        data = fetcher.get_bytes(link["url"])
    except Exception as exc:
        result["error"] = f"다운로드 실패: {type(exc).__name__}: {exc}"
        return result
    try:
        text, method, error = _extract((link.get("ext") or "").lower(), data)
    except Exception as exc:
        result["error"] = f"파싱 실패: {type(exc).__name__}: {exc}"
        return result
    result["text"] = text or ""
    result["method"] = method
    result["error"] = error
    result["chars"] = len(result["text"])
    return result
