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
            text_elements = page.extract_text_elements()
            text_boxes = [element for element in text_elements if element['object_type'] == 'char']

            for label in labels:
                label_found = False
                for text_box in text_boxes:
                    if label in text_box['text']:
                        label_found = True
                        label_x0 = text_box['x0']
                        label_x1 = text_box['x1']
                        label_top = text_box['top']
                        label_bottom = text_box['bottom']

                        # Define the search area around the label
                        search_x0 = label_x0 - tolerance
                        search_x1 = label_x1 + tolerance
                        search_top = label_top - tolerance
                        search_bottom = label_bottom + tolerance

                        # Check for blank regions in the search area
                        blank = True
                        for other_text_box in text_boxes:
                            if (search_x0 <= other_text_box['x1'] and search_x1 >= other_text_box['x0'] and
                                search_top <= other_text_box['bottom'] and search_bottom >= other_text_box['top']):
                                blank = False
                                break

                        if blank:
                            blank_regions.append({
                                'page_number': page.page_number + 1,
                                'x0': search_x0,
                                'x1': search_x1,
                                'top': search_top,
                                'bottom': search_bottom
                            })

                if not label_found:
                    print(f"Label '{label}' not found on page {page.page_number + 1}")

    return blank_regions
