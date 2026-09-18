import os
from pypdf import PdfReader, PdfWriter

def fill_form(pdf_path, values, out_path="tests/output/filled.pdf"):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.append(reader)
    writer.update_page_form_field_values(writer.pages[0], values)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return out_path
