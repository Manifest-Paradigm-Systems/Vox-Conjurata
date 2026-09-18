import pytest
from documents.inspect import inspect_pdf

def test_inspect_pdf_with_form_fields():
    # Use the existing fixture
    pdf_path = 'tests/fixtures/sample_form.pdf'
    result = inspect_pdf(pdf_path)
    assert isinstance(result, dict)
    assert 'form_fields' in result
    assert 'blank_regions' in result
    assert result['form_fields'] != {}

def test_inspect_pdf_with_blank_regions():
    # Use the existing fixture
    pdf_path = 'tests/fixtures/sample_flat.pdf'
    result = inspect_pdf(pdf_path)
    assert isinstance(result, dict)
    assert 'form_fields' in result
    assert 'blank_regions' in result
    assert result['form_fields'] == {}
