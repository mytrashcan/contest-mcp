"""첨부 파싱(hwpx·docx)과 빈 항목 채우기, 발표 문구 플래그."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

from contest_mcp import attachments, detail

BASE = dt.date(2026, 11, 13)

HTML_WITH_LINKS = """
<div>
  <a href="/files/공고문.hwpx">공고문.hwpx</a>
  <a href="https://example.com/a.pdf">요강.pdf</a>
  <a href="/files/중복.hwpx">중복.hwpx</a>
  <a href="/not/a/file.png">이미지</a>
</div>
"""

HWPX_SECTION = """<?xml version="1.0"?>
<hp:sec xmlns:hp="x">
  <hp:p><hp:run><hp:t>2026 공모전 요강</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>배점 : 창의성 40, 실현가능성 30, 완성도 30</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>제출물 : 기획서 5쪽, 시연영상 1편</hp:t></hp:run></hp:p>
</hp:sec>
"""


def _hwpx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", HWPX_SECTION)
        archive.writestr("Contents/header.xml", "<xml/>")
    return buffer.getvalue()


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>배점 : 50/50</w:t></w:r></w:p></w:document>")
    return buffer.getvalue()


CONTESTKOREA_STYLE = """
<div class="file">
  <li><img src="/img/sub/add_file.png"> <a href="file_dn.php?str_gbn=3&str_no=202610280017">2026년 제18회 요강.pdf</a></li>
  <a href="javascript:void(0)" class="attechFile">첨부파일.zip</a>
</div>
"""


def test_extract_links_uses_anchor_text_extension():
    """콘테스트코리아는 href에 확장자가 없고 앵커 텍스트에만 있다."""
    links = attachments.extract_links(CONTESTKOREA_STYLE, "https://www.contestkorea.com/sub/view.php?x=1")
    assert len(links) == 1
    assert links[0]["ext"] == ".pdf"
    assert links[0]["url"].startswith("https://www.contestkorea.com/sub/file_dn.php")
    assert "요강" in links[0]["name"]


def test_extract_links_skips_javascript_download():
    links = attachments.extract_links('<a href="javascript:void(0)">첨부파일.zip</a>')
    assert links == []


def test_zip_attachment_parses_inner_hwpx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("공고문.hwpx", _hwpx_bytes())
    payload = buffer.getvalue()

    class FakeFetcher:
        def get_bytes(self, url):
            return payload

    result = attachments.text_of(FakeFetcher(), {"url": "https://example.com/a.zip", "name": "a.zip", "ext": ".zip"})
    assert result["error"] is None
    assert result["method"].startswith("zip>")
    assert "배점 : 창의성 40" in result["text"]


def test_zip_without_supported_files_reports_reason():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")

    class FakeFetcher:
        def get_bytes(self, url):
            return buffer.getvalue()

    result = attachments.text_of(FakeFetcher(), {"url": "https://example.com/a.zip", "name": "a.zip", "ext": ".zip"})
    assert result["text"] == ""
    assert result["error"] and "지원 형식" in result["error"]


def test_extract_links_dedupes_and_resolves_relative():
    links = attachments.extract_links(HTML_WITH_LINKS, "https://example.com/contest/1")
    urls = [link["url"] for link in links]
    assert urls[0] == "https://example.com/files/공고문.hwpx"
    assert "https://example.com/a.pdf" in urls
    assert all(url.endswith((".hwpx", ".pdf")) for url in urls)
    assert len(urls) == len(set(urls))


def test_hwpx_text_extracts_paragraphs():
    text = attachments.hwpx_text(_hwpx_bytes())
    assert "2026 공모전 요강" in text
    assert "배점 : 창의성 40" in text
    assert "\n" in text


def test_docx_text_extracts_paragraphs():
    assert "배점 : 50/50" in attachments.docx_text(_docx_bytes())


def test_enrich_fills_only_missing_fields():
    parsed = {
        "fields": {"award": {"label": "시상내역", "evidence": "대상 500만원"}},
        "missing": ["scoring", "submission"],
    }
    detail.enrich_with_text(parsed, "배점 : 창의성 40, 실현가능성 30\n제출물 : 기획서 5쪽")
    assert "scoring" in parsed["fields"]
    assert parsed["fields"]["scoring"]["source"] == "attachment"
    assert "scoring" not in parsed["missing"]
    assert parsed["fields"]["award"]["evidence"] == "대상 500만원"  # 기존 항목은 그대로
    assert "submission" in parsed["fields"]


def test_enrich_fills_eligibility_from_participant_label():
    """첨부 PDF는 '참가대상' 표기를 쓴다(실측) — 자격 라벨에 포함돼야 한다."""
    parsed = {"fields": {}, "missing": ["eligibility"]}
    detail.enrich_with_text(parsed, "5. 참가대상: 전국 초등학생, 중학생, 고등학생, 청소년")
    assert "eligibility" in parsed["fields"]
    assert "초등학생" in parsed["fields"]["eligibility"]["evidence"]


def test_navigation_label_is_skipped_for_real_condition():
    """'참가대상 | 주최.주관 | 총상금' 같은 네비게이션 잔재는 근거로 쓰지 않는다."""
    html = "<html><body>참가대상 | 주최.주관 | 총상금 참가대상: 전국 대학생 및 대학원생</body></html>"
    parsed = detail.parse(html, "https://example.com/1", BASE)
    evidence = parsed["fields"]["eligibility"]["evidence"]
    assert "대학생" in evidence
    assert "|" not in evidence[:14]


def test_presentation_flag_yes_and_no():
    html_yes = "<html><body><div>본선 발표는 대면으로 진행합니다</div></body></html>"
    parsed = detail.parse(html_yes, "https://example.com/1", BASE)
    assert parsed["presentation"]["flag"] == "yes"
    assert parsed["presentation"]["keyword"] in {"발표는", "본선 발표"}

    html_no = "<html><body><div>시상내역 : 대상 500만원</div></body></html>"
    parsed_no = detail.parse(html_no, "https://example.com/1", BASE)
    assert parsed_no["presentation"]["flag"] == "no"


def test_parse_exposes_attachment_links():
    parsed = detail.parse(HTML_WITH_LINKS, "https://example.com/contest/1", BASE)
    exts = {link["ext"] for link in parsed["attachments"]}
    assert exts == {".hwpx", ".pdf"}


def test_text_of_unsupported_extension_reports_reason():
    class FakeFetcher:
        def get_bytes(self, url):
            return b"binary"

    result = attachments.text_of(FakeFetcher(), {"url": "https://example.com/a.png", "name": "a.png", "ext": ".png"})
    assert result["text"] == ""
    assert "지원하지 않는 형식" in result["error"]


def test_text_of_hwpx_via_fetcher():
    class FakeFetcher:
        def get_bytes(self, url):
            return _hwpx_bytes()

    result = attachments.text_of(FakeFetcher(), {"url": "https://example.com/a.hwpx", "name": "a.hwpx", "ext": ".hwpx"})
    assert result["method"] == "zip+xml"
    assert "배점" in result["text"]
    assert result["chars"] > 10


def test_pdf_text_asks_pdftotext_for_stdout(monkeypatch):
    """pdftotext는 출력 경로를 생략하면 파일로 쓴다 → '-'를 넘겨야 stdout으로 온다."""
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command

        class Result:
            stdout = "배점 : 50/50"
            stderr = ""

        return Result()

    monkeypatch.setattr(attachments.shutil, "which", lambda name: "/usr/bin/pdftotext")
    monkeypatch.setattr(attachments.subprocess, "run", fake_run)
    text, error = attachments._pdf_text(b"%PDF-1.6 fake")
    assert error is None
    assert text == "배점 : 50/50"
    assert captured["command"][-1] == "-"


def test_pdf_without_pdftotext_reports_reason(monkeypatch):
    monkeypatch.setattr(attachments.shutil, "which", lambda name: None)
    text, error = attachments._pdf_text(b"%PDF-1.6 fake")
    assert text == ""
    assert "pdftotext" in error
