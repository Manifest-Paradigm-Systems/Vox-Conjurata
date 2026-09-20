import pdfplumber

def find_blank_regions(pdf_path, labels, tolerance=10):
    """
    Detect blank areas near labels in a PDF using pdfplumber.

    :param pdf_path: Path to the PDF file.
    :param labels: List of labels to find blank regions near.
    :param tolerance: Tolerance around the labels to consider for blank regions.
    :return: List of blank regions near the labels.
    """
    blank_regions = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract text elements using the specified method
            text_elements = page.extract_text_lines()
            
            for label in labels:
                label_found = False
                for element in text_elements:
                    # Check if the label exists as a substring in the text
                    if label in element.get('text', ''):
                        label_found = True
                        
                        # Get the bounding box of the label
                        if 'bbox' not in element:
                            continue
                        label_bbox = element['bbox']
                        x0, y0, x1, y1 = label_bbox
                        
                        # Define the search area after the label
                        search_x0 = x1  # Start right after the label
                        search_x1 = x1 + tolerance  # Extend tolerance to the right
                        search_y0 = y0 - tolerance  # Extend tolerance up
                        search_y1 = y1 + tolerance  # Extend tolerance down
                        
                        # Find the next non-blank text element
                        next_text_element = None
                        for next_element in text_elements:
                            # Skip if it's the same element
                            if next_element == element:
                                continue
                            
                            # Check if it's positioned to the right of the label
                            if next_element.get('bbox', [0, 0, 0, 0])[0] > x1:
                                # Check if it's on the same vertical line
                                if abs(next_element.get('bbox', [0, 0, 0, 0])[1] - y0) < tolerance:
                                    next_text_element = next_element
                                    break
                        
                        # If no next text element found, extend to page end
                        if not next_text_element:
                            blank_regions.append((search_x0, y0, page.width, y1))
                        else:
                            # Define blank region up to the next text element
                            next_x0 = next_element.get('bbox', [0, 0, 0, 0])[0]
                            blank_regions.append((search_x0, y0, next_x0, y1))
                
                if not label_found:
                    print(f"Label '{label}' not found on page {page.page_number + 1}")

    return blank_regions