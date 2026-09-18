from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

def generate_rent_receipt(output_path, tenant_name, street_address):
    # Create a PDF with tenant details
    c = canvas.Canvas(output_path, pagesize=letter)
    width, height = letter

    # Default values for rent amount and due date
    rent_amount = '$1000'
    due_date = '2023-10-01'

    # Add content to the PDF
    c.drawString(100, height - 100, f"Rent Receipt for {tenant_name}")
    c.drawString(100, height - 120, f"Address: {street_address}")
    c.drawString(100, height - 140, f"Rent Amount: {rent_amount}")
    c.drawString(100, height - 160, f"Due Date: {due_date}")

    # Save the PDF
    c.showPage()
    c.save()
