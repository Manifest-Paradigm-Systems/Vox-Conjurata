from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject


def add_signature_widget(pdf_path, out_path, field_name="Signature1", position=(100, 100), size=(200, 100)):
    # Open the existing PDF in binary mode
    with open(pdf_path, "rb") as in_file:
        reader = PdfReader(in_file)
        writer = PdfWriter()

        # Copy all pages from the reader to the writer
        for page in reader.pages:
            writer.add_page(page)

        # Create a signature annotation
        signature_annotation = {
            "/Type": "/Annot",
            "/Subtype": "/Widget",
            "/Rect": RectangleObject([position[0], position[1], position[0] + size[0], position[1] + size[1]]),
            "/FT": "/Sig",
            "/T": field_name,
            "/F": 4,
        }

        # Add the signature annotation to the first page
        page = writer.pages[0]
        page["/Annots"] = page.get("/Annots", []) + [writer._add_object(signature_annotation)]

        # Write the updated PDF to a new file
        with open(out_path, "wb") as out_file:
            writer.write(out_file)
    return out_path
