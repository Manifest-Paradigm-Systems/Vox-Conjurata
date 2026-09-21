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
        # Extract text elements
        text_elements = page.extract_text_lines()
        
        for element in text_elements:
            # Check if the label exists as a substring in the text
            line_text = element.get('text', '')
            if label in line_text:
                # Get the bounding box of the label
                if 'bbox' not in element:
                    continue
                label_bbox = element['bbox']
                x0, y0, x1, y1 = label_bbox
                
                # Define blank region from end of label to page width
                blank_regions.append((x1, y0, page.width, y1))
    
    return blank_regions