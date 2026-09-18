import os
from reportlab.pdfgen import canvas
from pypdf import PdfReader

def create_sample_form(output_path):
    # Create a PDF with a form field using ReportLab
    c = canvas.Canvas(output_path, pagesize=(595.27, 841.89))  # A4 size
    c.drawString(72, 720, "Sample Form")
    c.acroForm.textfield(name="name", tooltip="Name", x=72, y=680,
                         width=200, height=20, borderStyle="inset", forceBorder=True)
    c.showPage()
    c.save()

    # Verify the form field is created
    reader = PdfReader(output_path)
    fields = reader.get_fields()
    if not fields:
        raise Exception("Form field not created correctly")

def create_sample_flat(output_path):
    # Create a PDF with no form fields using ReportLab
    c = canvas.Canvas(output_path, pagesize=(595.27, 841.89))  # A4 size
    c.drawString(72, 720, "Sample Flat PDF")
    c.drawString(72, 680, "Signature:")
    c.showPage()
    c.save()

    # Verify no form fields are created
    reader = PdfReader(output_path)
    fields = reader.get_fields()
    if fields:
        raise Exception("Form fields created incorrectly")

def main():
    # Ensure the fixtures directory exists
    fixtures_dir = "tests/fixtures"
    os.makedirs(fixtures_dir, exist_ok=True)

    # Ensure the output directory exists
    output_dir = "tests/output"
    os.makedirs(output_dir, exist_ok=True)

    # Create the sample form PDF
    sample_form_path = os.path.join(fixtures_dir, "sample_form.pdf")
    create_sample_form(sample_form_path)

    # Create the sample flat PDF
    sample_flat_path = os.path.join(fixtures_dir, "sample_flat.pdf")
    create_sample_flat(sample_flat_path)


if __name__ == "__main__":
    main()
