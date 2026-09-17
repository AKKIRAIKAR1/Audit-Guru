import io
import warnings
from pathlib import Path

import pymupdf
from PIL import Image, UnidentifiedImageError
from app.config import settings


class InvalidDocument(ValueError):
    pass


def validate(content: bytes, filename: str):
    cfg = settings()
    if not content:
        raise InvalidDocument('The document is empty.')
    if len(content) > cfg.max_file_mb * 1024 * 1024:
        raise InvalidDocument(f'The maximum file size is {cfg.max_file_mb} MB.')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf', '.jpg', '.jpeg', '.png'}:
        raise InvalidDocument('Supported formats: PDF, JPG, JPEG and PNG.')
    try:
        if suffix == '.pdf':
            if not content.startswith(b'%PDF-'):
                raise InvalidDocument('The file does not contain a valid PDF header.')
            with pymupdf.open(stream=content, filetype='pdf') as doc:
                if doc.needs_pass:
                    raise InvalidDocument('Password-protected PDFs are not supported.')
                if doc.is_repaired:
                    raise InvalidDocument('The PDF is damaged. Export a new copy and retry.')
                if not 1 <= len(doc) <= cfg.max_pages:
                    raise InvalidDocument(f'Documents must contain 1–{cfg.max_pages} pages.')
                for page in doc:
                    if page.rect.width <= 0 or page.rect.height <= 0:
                        raise InvalidDocument('The PDF has invalid page dimensions.')
                return 'application/pdf', len(doc)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as img:
                expected = 'PNG' if suffix == '.png' else 'JPEG'
                if img.format != expected:
                    raise InvalidDocument('The file extension does not match its contents.')
                if img.width * img.height > cfg.max_image_pixels:
                    raise InvalidDocument('The image resolution exceeds the processing limit.')
                if getattr(img, 'n_frames', 1) != 1:
                    raise InvalidDocument('Animated images are not supported.')
                img.verify()
            with Image.open(io.BytesIO(content)) as img:
                img.load()
        return ('image/png' if suffix == '.png' else 'image/jpeg'), 1
    except InvalidDocument:
        raise
    except (pymupdf.FileDataError, RuntimeError, ValueError, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidDocument('The document is corrupted or unreadable.') from exc
