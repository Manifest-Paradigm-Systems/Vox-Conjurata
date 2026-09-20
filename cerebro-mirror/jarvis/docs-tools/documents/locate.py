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
        for page in pdf.pages:
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
                    
                    # Find the next non-blank text element
                    next_text_element = None
                    for next_element in text_elements:
                        # Skip if it's the same element
                        if next_element == element:
                            continue
                        
                        # Check if it's positioned to the right of the label
                        if next_element.get('bbox', [0, 0, 0, 0])[0] > x1:
                            # Check if it's on the same vertical line
                            if abs(next_element.get('bbox', [0, 0, 0, 0])[1] - y0) < 5:
                                next_text_element = next_element
                                break
                    
                    # Define blank region
                    if not next_text_element:
                        # Extend to page end if no next text element found
                        blank_regions.append((x1, y0, page.width, y1))
                    else:
                        # Define blank region up to the next text element
                        next_x0 = next_element.get('bbox', [0, 0, 0, 0])[0]
                        blank_regions.append((x1, y0, next_x0, y1))
    
    return blank_regions