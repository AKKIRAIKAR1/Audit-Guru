"""Isolated OCR provider process: bounded by the parent process timeout."""
import json
import sys
from importlib.metadata import version

import numpy as np
from PIL import Image


def recognize(provider, image_path, language):
    if provider == 'tesseract':
        import pytesseract
        result = pytesseract.image_to_data(Image.open(image_path), lang=language,
                                           output_type=pytesseract.Output.DICT)
        words = []
        for i, text in enumerate(result['text']):
            if not text.strip() or float(result['conf'][i]) < 0:
                continue
            x, y, w, h = (int(result[key][i]) for key in ('left', 'top', 'width', 'height'))
            words.append({'text': text, 'confidence': float(result['conf'][i]) / 100,
                          'bounding_box': [x, y, x + w, y + h], 'granularity': 'word',
                          'line_id': f"{result['block_num'][i]}:{result['par_num'][i]}:{result['line_num'][i]}"})
        return words, str(pytesseract.get_tesseract_version())
    if provider == 'paddle':
        from paddleocr import PaddleOCR
        model = PaddleOCR(lang=language, use_doc_orientation_classify=False,
                          use_doc_unwarping=False, use_textline_orientation=False)
        words = []
        for result in model.predict(np.asarray(Image.open(image_path).convert('RGB'))):
            for i, (text, score, polygon) in enumerate(zip(result['rec_texts'], result['rec_scores'], result['rec_polys'])):
                points = np.asarray(polygon)
                words.append({'text': text, 'confidence': float(score),
                              'bounding_box': [float(points[:, 0].min()), float(points[:, 1].min()),
                                               float(points[:, 0].max()), float(points[:, 1].max())],
                              'granularity': 'line', 'line_id': str(i)})
        return words, version('paddleocr')
    raise ValueError('Unsupported OCR provider')


if __name__ == '__main__':
    words, engine_version = recognize(sys.argv[1], sys.argv[2], sys.argv[4])
    with open(sys.argv[3], 'w', encoding='utf-8') as output:
        json.dump({'words': words, 'version': engine_version}, output)
