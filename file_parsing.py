import io
from typing import BinaryIO


def extract_text_from_file(file_stream: BinaryIO, filename: str) -> str:  # noqa: C901
    """Extract text from common file types with safe fallbacks.

    - file_stream: file-like with .read() or raw bytes
    - filename: used to detect type by extension
    Returns extracted text or empty string on failure.
    """
    name = (filename or '').lower()

    # Read bytes safely
    data = b''
    if isinstance(file_stream, (bytes, bytearray)):
        data = bytes(file_stream)
    elif hasattr(file_stream, 'read'):
        try:
            data = file_stream.read()
        except Exception:
            data = b''
    else:
        data = b''

    # Plain text / CSV
    if name.endswith('.txt') or name.endswith('.csv'):
        try:
            return data.decode('utf-8')
        except Exception:
            try:
                return data.decode('latin-1')
            except Exception:
                return ''

    # DOCX
    if name.endswith('.docx'):
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
            return '\n'.join(p.text for p in doc.paragraphs if p.text)
        except Exception:
            return ''

    # PDF: prefer pdfminer.six, else OCR via pdf2image+pytesseract
    if name.endswith('.pdf'):
        # try pdfminer.six
        try:
            from pdfminer.high_level import extract_text
            try:
                return extract_text(io.BytesIO(data)) or ''
            except Exception:
                # fallback to writing temp file
                import tempfile
                import os
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tf:
                    tf.write(data)
                    path = tf.name
                try:
                    return extract_text(path) or ''
                finally:
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
        except Exception:
            # try OCR path
            try:
                from pdf2image import convert_from_bytes
                import pytesseract
                pages = convert_from_bytes(data)
                texts = []
                for p in pages:
                    texts.append(pytesseract.image_to_string(p))
                return '\n'.join(texts)
            except Exception:
                return ''

    # Image OCR
    if any(name.endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.tiff', '.bmp')):
        try:
            from PIL import Image
            import pytesseract
            img = Image.open(io.BytesIO(data))
            return pytesseract.image_to_string(img)
        except Exception:
            return ''

    return ''
