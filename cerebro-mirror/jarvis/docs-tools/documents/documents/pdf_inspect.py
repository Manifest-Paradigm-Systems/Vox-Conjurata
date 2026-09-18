import PyPDF2
import pdfplumber


def inspect_pdf(pdf_path):
    # Initialize the result dictionary
    result = {
        'form_fields': [],
        'blank_regions': []
    }

    # Use PyPDF2 to detect form fields
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page_num in range(len(reader.pages)):
                fields = reader.get_fields()
                for field in fields:
                    result['form_fields'].append(field)
    except Exception as e:
        print(f"An error occurred while reading the PDF with PyPDF2: {e}")

    # Use pdfplumber to detect blank regions
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if not text:
                    result['blank_regions'].append(page_number)  # Page numbers are 1-based
    except Exception as e:
        print(f"An error occurred while reading the PDF with pdfplumber: {e}")

    return result
