"""Tests for output styles (#7) and document attachment inlining (#10)."""

from __future__ import annotations

from aio.web import _extract_doc_text


def _service(tmp_path, monkeypatch):
    from tests.test_web import _service as svc
    return svc(tmp_path, monkeypatch)


def test_output_style_applied_to_system_prompt(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    assert svc.info()["output_style"] == "default"
    out = svc.set_output_style("concise")
    assert out["output_style"] == "concise"
    assert "Output style: concise" in svc.agent.system_prompt
    # unknown style is ignored
    svc.set_output_style("bogus")
    assert svc.info()["output_style"] == "concise"


def test_extract_text_file():
    assert _extract_doc_text("notes.txt", b"plain text body") == "plain text body"
    assert "code" in _extract_doc_text("a.py", b"print('code')")


def test_extract_pdf_text():
    # a tiny hand-rolled PDF-ish blob with a Tj string
    pdf = b"%PDF-1.4\n1 0 obj<<>>stream\nBT (Hello PDF) Tj ET\nendstream"
    out = _extract_doc_text("doc.pdf", pdf)
    assert "Hello PDF" in out


def test_inline_files_appends_text(tmp_path, monkeypatch):
    import base64
    svc = _service(tmp_path, monkeypatch)
    files = [{"name": "spec.txt", "data": base64.b64encode(b"SPEC CONTENT").decode()}]
    msg = svc._inline_files("please read", files)
    assert "please read" in msg and "spec.txt" in msg and "SPEC CONTENT" in msg
