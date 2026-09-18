import PyPDF2
import pdfplumber
from documents.pdf_inspect import inspect_pdf

def test_inspect_pdf():
    pdf_path = 'path/to/test.pdf'
    result = inspect_pdf(pdf_path)
    assert isinstance(result, dict)
    assert 'form_fields' in result
    assert 'blank_regions' in result
