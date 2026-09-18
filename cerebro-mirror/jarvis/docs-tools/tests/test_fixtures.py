import os
from PyPDF2 import PdfReader

def test_sample_form_pdf():
    path = "tests/fixtures/sample_form.pdf"
    reader = PdfReader(path)
    assert len(reader.pages) == 1
    fields = reader.get_fields()
    assert fields
    assert "SampleField" in fields

def test_sample_flat_pdf():
    path = "tests/fixtures/sample_flat.pdf"
    reader = PdfReader(path)
    assert len(reader.pages) == 1
    fields = reader.get_fields()
    assert not fields
    page = reader.pages[0]
    text = page.extract_text()
    assert "Signature:" in text
