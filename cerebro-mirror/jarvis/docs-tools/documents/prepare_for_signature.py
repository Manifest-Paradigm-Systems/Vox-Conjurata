from .fill import fill_form
from .signature import add_signature_widget
from .overlay import overlay_text
from .locate import find_blank_regions
from .receipt import generate_rent_receipt
import fitz  # PyMuPDF
import shutil

def prepare(input_pdf_path: str, form_data: dict, output_pdf_path: str) -> None:
    """
    Prepare a PDF for signature by filling form fields, adding signature fields,
    and optionally overlaying text.

    :param input_pdf_path: Path to the input PDF template.
    :param form_data: Dictionary of form field names and values.
    :param output_pdf_path: Path where the output PDF will be saved.
    """
    # Step 1: Fill the form fields
    filled_pdf_path = "temp_filled.pdf"
    fill_form(input_pdf_path, filled_pdf_path, form_data)

    # Step 2: Add signature widget
    signed_pdf_path = "temp_signed.pdf"
    add_signature_widget(filled_pdf_path, signed_pdf_path, (100, 100))

    # Step 3: Move final PDF to output path
    shutil.move(signed_pdf_path, output_pdf_path)

    # Step 4: Generate a rent receipt
    receipt_path = "receipt.pdf"
    generate_rent_receipt(receipt_path, form_data)

    print(f"Document prepared for signature and saved to {output_pdf_path}")
    print(f"Receipt generated at {receipt_path}")