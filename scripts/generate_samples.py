"""Generate synthetic supplier invoices (clean and anomaly) as realistic PNG images."""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def draw_invoice(
    output_path: str,
    supplier_name: str,
    invoice_number: str,
    invoice_date: str,
    items: list,
    subtotal: float,
    tax: float,
    total: float,
    banner_text: str = "",
) -> None:
    """Render a clean, high-contrast, realistic commercial invoice image."""
    width = 900
    height = 1100
    background_color = (255, 255, 255)
    text_color = (30, 41, 59)
    muted_color = (100, 116, 139)
    accent_color = (15, 23, 42)
    border_color = (226, 232, 240)
    table_header_bg = (241, 245, 249)

    image = Image.new("RGB", (width, height), color=background_color)
    draw = ImageDraw.Draw(image)

    # Use default font or load standard TTF if available
    try:
        font_large = ImageFont.truetype("arial.ttf", 28)
        font_header = ImageFont.truetype("arial.ttf", 20)
        font_body = ImageFont.truetype("arial.ttf", 15)
        font_bold = ImageFont.truetype("arialbd.ttf", 15)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except IOError:
        # Fallback to load_default
        font_large = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_body = ImageFont.load_default()
        font_bold = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Outer decorative header bar
    draw.rectangle([(0, 0), (width, 16)], fill=(37, 99, 235))

    # Supplier Header
    y = 50
    draw.text((60, y), supplier_name.upper(), fill=accent_color, font=font_large)
    y += 38
    draw.text((60, y), "Industrial Lighting & Electrical Wholesale Corp.", fill=muted_color, font=font_small)
    y += 18
    draw.text((60, y), "1042 Commerce Boulevard, Logistics District, Suite 400", fill=muted_color, font=font_small)
    y += 18
    draw.text((60, y), "billing@lumiere-supply.com | +1 (800) 555-0199", fill=muted_color, font=font_small)

    # Invoice Title & Meta (Right side)
    draw.text((600, 50), "COMMERCIAL INVOICE", fill=accent_color, font=font_header)
    draw.text((600, 85), f"Invoice #: {invoice_number}", fill=text_color, font=font_bold)
    draw.text((600, 110), f"Date: {invoice_date}", fill=muted_color, font=font_body)
    draw.text((600, 132), "Terms: Net 30 Days", fill=muted_color, font=font_body)
    draw.text((600, 154), "Currency: USD ($)", fill=muted_color, font=font_body)

    # Optional banner (e.g. for anomaly flag context in testing)
    if banner_text:
        draw.rectangle([(60, 185), (width - 60, 220)], fill=(254, 242, 242), outline=(239, 68, 68))
        draw.text((75, 196), banner_text, fill=(185, 28, 28), font=font_bold)
        y = 240
    else:
        y = 200

    # Billed To Section
    draw.rectangle([(60, y), (width - 60, y + 80)], fill=(248, 250, 252), outline=border_color)
    draw.text((80, y + 12), "BILLED TO:", fill=muted_color, font=font_small)
    draw.text((80, y + 30), "StockOps Automated Fulfillment Hub 07", fill=accent_color, font=font_bold)
    draw.text((80, y + 50), "770 Innovation Way, dock 12B, Warehouse Operations", fill=muted_color, font=font_small)

    y += 110

    # Line Items Table Header
    draw.rectangle([(60, y), (width - 60, y + 36)], fill=table_header_bg, outline=border_color)
    draw.text((80, y + 10), "SKU", fill=accent_color, font=font_bold)
    draw.text((260, y + 10), "DESCRIPTION", fill=accent_color, font=font_bold)
    draw.text((580, y + 10), "QTY", fill=accent_color, font=font_bold)
    draw.text((660, y + 10), "UNIT PRICE", fill=accent_color, font=font_bold)
    draw.text((780, y + 10), "TOTAL", fill=accent_color, font=font_bold)

    y += 36

    # Line Items Rows
    for item in items:
        draw.rectangle([(60, y), (width - 60, y + 42)], outline=border_color)
        draw.text((80, y + 12), item["sku"], fill=text_color, font=font_bold)
        draw.text((260, y + 12), item["description"][:38], fill=text_color, font=font_body)
        draw.text((590, y + 12), str(item["quantity"]), fill=text_color, font=font_body)
        draw.text((670, y + 12), f"${item['unit_price']:.2f}", fill=text_color, font=font_body)
        draw.text((780, y + 12), f"${item['line_total']:.2f}", fill=text_color, font=font_bold)
        y += 42

    # Totals Section
    y += 30
    draw.line([(550, y), (width - 60, y)], fill=border_color, width=1)
    y += 12

    draw.text((600, y), "Subtotal:", fill=muted_color, font=font_body)
    draw.text((780, y), f"${subtotal:.2f}", fill=text_color, font=font_body)
    y += 24

    draw.text((600, y), "Tax / VAT (0%):", fill=muted_color, font=font_body)
    draw.text((780, y), f"${tax:.2f}", fill=text_color, font=font_body)
    y += 28

    draw.rectangle([(550, y), (width - 60, y + 40)], fill=(241, 245, 249))
    draw.text((570, y + 10), "TOTAL AMOUNT:", fill=accent_color, font=font_bold)
    draw.text((780, y + 10), f"${total:.2f}", fill=(37, 99, 235), font=font_bold)

    # Footer Notes
    y = 960
    draw.line([(60, y), (width - 60, y)], fill=border_color, width=1)
    y += 20
    draw.text((60, y), "PAYMENT INSTRUCTIONS:", fill=accent_color, font=font_bold)
    y += 20
    draw.text((60, y), "Wire Transfer to: First National Commerce Bank | Routing: 121000358 | Account: 9884-2201-99", fill=muted_color, font=font_small)
    y += 18
    draw.text((60, y), "Thank you for your business. For inventory inquiries, contact dispatch@lumiere-supply.com", fill=muted_color, font=font_small)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
    print(f"Generated invoice image: {output_path}")


def generate_all_samples():
    """Generate both valid and anomaly synthetic test invoice images."""
    base_dir = Path(__file__).resolve().parent.parent / "sample_data"

    # 1. Clean valid invoice (matches catalog price of $14.50)
    draw_invoice(
        output_path=str(base_dir / "valid_invoice.png"),
        supplier_name="Lumiere Industrial Supply",
        invoice_number="INV-2026-101",
        invoice_date="2026-10-24",
        items=[
            {
                "sku": "LED-PNL-6060-40W",
                "description": "Backlit LED Panel 60x60cm 40W 6500K",
                "quantity": 10,
                "unit_price": 14.50,
                "line_total": 145.00,
            }
        ],
        subtotal=145.00,
        tax=0.00,
        total=145.00,
        banner_text="CLEAN CATALOG REORDER - AUTOMATIC STOCK RECEIPT",
    )

    # 2. Anomaly invoice (+28.57% spike: billed $36.00 vs $28.00 catalog cost)
    draw_invoice(
        output_path=str(base_dir / "anomaly_invoice.png"),
        supplier_name="Lumiere Industrial Supply",
        invoice_number="INV-2026-102",
        invoice_date="2026-10-24",
        items=[
            {
                "sku": "FL-COB-100W-IP65",
                "description": "Outdoor Floodlight 100W IP65 Aluminum",
                "quantity": 5,
                "unit_price": 36.00,
                "line_total": 180.00,
            }
        ],
        subtotal=180.00,
        tax=0.00,
        total=180.00,
        banner_text="SUPPLY ALERT: UNIT PRICE SURCHARGE APPLIED (+28.57%)",
    )


if __name__ == "__main__":
    generate_all_samples()
