import pdfplumber

def find_blank_regions(pdf_path, label):
    """
    Detect blank areas near a label in a PDF using pdfplumber.

    :param pdf_path: Path to the PDF file.
    :param label: The exact label string to find.
    :return: List of blank regions near the label.
    """
    blank_regions = []

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]  # Only use the first page
        # Extract text lines
        text_lines = page.extract_text_lines()
        
        for line in text_lines:
            # Check if the label exists as a substring in the line
            line_text = line.get('text', '')
            if label in line_text:
                # Get the bounding box of the line
                if 'bbox' not in line:
                    continue
                line_bbox = line['bbox']
                x0, y0, x1, y1 = line_bbox
                
                # Return the entire line as the blank region
                blank_regions.append((x0, y0, x1, y1))
    
    return blank_regions
