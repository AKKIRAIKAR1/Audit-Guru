import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.jobs import process_job


@pytest.mark.skipif(shutil.which('tesseract') is None, reason='Native Tesseract executable is not installed')
def test_real_scanned_invoice(client, monkeypatch):
    monkeypatch.setattr(settings(), 'ocr_provider', 'tesseract')
    monkeypatch.setattr(settings(), 'ocr_fallback', 'tesseract')
    image = Image.new('RGB', (1400, 800), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=45)
    draw.multiline_text((70, 70), 'ABC SUPPLIES\nInvoice INV-10234\nSubtotal 50000\nGST 9000\nTotal 59000', fill='black', font=font, spacing=25)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    auth = {'X-API-Key':'test-a'}
    response = client.post('/api/v1/documents', headers=auth, files={'file':('scan.png',stream.getvalue(),'image/png')})
    doc = response.json()
    process_job(doc['job_id'])
    result = client.get('/api/v1/ocr/result/'+doc['id'], headers=auth)
    assert result.status_code == 200
    assert '59000' in result.json()['text']
    assert result.json()['pages'][0]['ocr_engine'] == 'tesseract'
    assert result.json()['pages'][0]['words']
