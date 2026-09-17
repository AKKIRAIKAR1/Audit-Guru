import io
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from app.config import settings


class ProviderUnavailable(RuntimeError):
    pass


def run_provider(image):
    cfg = settings()
    providers = list(dict.fromkeys([cfg.ocr_provider, cfg.ocr_fallback]))
    failures = []
    with tempfile.TemporaryDirectory(prefix='invoice-ocr-') as folder:
        source, target = Path(folder) / 'page.png', Path(folder) / 'result.json'
        image.save(source)
        for provider in filter(None, providers):
            language = cfg.paddle_language if provider == 'paddle' else cfg.ocr_language
            try:
                subprocess.run([sys.executable, '-m', 'app.engine_cli', provider, str(source),
                                str(target), language], check=True, capture_output=True,
                               timeout=cfg.ocr_timeout_seconds)
                result = json.loads(target.read_text(encoding='utf-8'))
                return result['words'], provider, result['version'], failures
            except (subprocess.SubprocessError, OSError, ValueError, KeyError):
                failures.append(f'{provider} was unavailable or timed out')
    raise ProviderUnavailable('OCR providers unavailable or timed out. Check worker installation and language models.')


def text_from_words(words):
    lines = {}
    for word in words:
        lines.setdefault(word.get('line_id', '0'), []).append(word['text'])
    return '\n'.join(' '.join(line) for line in lines.values())


def ruled_tables(gray, words):
    """Detect ruled table regions; cells/semantics remain the extraction module's job."""
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 31, 15)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, gray.shape[1] // 25), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, gray.shape[0] // 25))))
    contours, _ = cv2.findContours(cv2.bitwise_or(horizontal, vertical), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        intersections = cv2.bitwise_and(horizontal[y:y+h, x:x+w], vertical[y:y+h, x:x+w])
        count, _ = cv2.connectedComponents(intersections)
        if w > gray.shape[1] * .2 and h > 40 and count >= 7:
            regions.append({'bounding_box': [x, y, x+w, y+h], 'method': 'ruled_lines',
                            'word_indices': [i for i, word in enumerate(words)
                                             if x <= word['bounding_box'][0] <= x+w
                                             and y <= word['bounding_box'][1] <= y+h]})
    return regions


def image_page(image, number):
    started = time.monotonic()
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((5000, 5000))
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    processed = Image.fromarray(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray))
    words, provider, version, warnings = run_provider(processed)
    confidence = sum(w['confidence'] for w in words) / len(words) if words else 0.0
    if sharpness < settings().blur_threshold:
        warnings.append('Image appears blurry; check the original document.')
    if not words:
        warnings.append('No text detected; manual review required.')
    elif confidence < settings().quality_confidence_threshold:
        warnings.append('Low OCR confidence; manual review required.')
    return {'page_number': number, 'text': text_from_words(words), 'words': words,
            'width': image.width, 'height': image.height, 'coordinate_unit': 'pixels',
            'coordinate_reference': 'EXIF-oriented image resized to the reported width and height',
            'source': 'ocr', 'ocr_engine': provider, 'ocr_version': version,
            'confidence': confidence, 'sharpness': sharpness, 'warnings': warnings,
            'review_required': not words or confidence < settings().quality_confidence_threshold or sharpness < settings().blur_threshold,
            'tables': ruled_tables(gray, words), 'processing_time': time.monotonic() - started}


def pages(content, media_type):
    if media_type != 'application/pdf':
        with Image.open(io.BytesIO(content)) as image:
            yield image_page(image, 1)
        return
    with pymupdf.open(stream=content, filetype='pdf') as doc:
        for number, page in enumerate(doc, 1):
            started = time.monotonic()
            native_words = page.get_text('words', sort=True)
            # A large embedded scan plus a small digital header must still use OCR.
            image_coverage = sum(pymupdf.Rect(info['bbox']).get_area() for info in page.get_image_info()) / max(1, page.rect.get_area())
            native_text = page.get_text('text', sort=True).strip()
            readable = sum(c.isalnum() for c in native_text) >= 12 and '\ufffd' not in native_text
            if native_words and readable and image_coverage < .5:
                words = [{'text': w[4], 'confidence': None, 'bounding_box': list(w[:4]),
                          'granularity': 'word', 'line_id': f'{w[5]}:{w[6]}'} for w in native_words]
                tables = [{'bounding_box': list(table.bbox), 'rows': table.extract(), 'method': 'pymupdf'}
                          for table in page.find_tables().tables]
                yield {'page_number': number, 'text': native_text, 'words': words,
                       'width': page.cropbox.width, 'height': page.cropbox.height, 'coordinate_unit': 'points',
                       'coordinate_reference': 'unrotated PDF page', 'source': 'digital_pdf',
                       'ocr_engine': 'pymupdf', 'ocr_version': pymupdf.VersionBind,
                       'confidence': None, 'sharpness': None, 'warnings': [], 'review_required': False,
                       'tables': tables, 'processing_time': time.monotonic() - started}
            else:
                scale = min(2.5, math.sqrt(settings().max_image_pixels / max(1, page.rect.get_area())))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
                yield image_page(Image.frombytes('RGB', [pix.width, pix.height], pix.samples), number)
