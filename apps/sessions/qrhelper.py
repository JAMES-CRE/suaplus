import io, base64
import qrcode
from qrcode.image.svg import SvgPathImage

def qr_data_uri(text: str) -> str:
    """Generates a clean SVG QR code as a data URI."""
    img = qrcode.make(text, image_factory=SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()