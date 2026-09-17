import io
import time
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest
from PIL import Image
from sqlalchemy import select, update

from app import ocr
from app.config import settings
from app.db import Job, OCRResult, Session
from app.jobs import process_job
from app.validation import InvalidDocument, validate

AUTH = {'X-API-Key': 'test-a'}
OTHER = {'X-API-Key': 'test-b'}


def pdf(pages=2):
    with pymupdf.open() as doc:
        for number in range(pages):
            page = doc.new_page()
            page.insert_text((70, 80), f'ABC SUPPLIES PVT LTD\nInvoice INV-10234 Page {number+1}\nSubtotal: 50000\nGST: 9000\nTotal: 59000')
        return doc.tobytes()


def png():
    stream = io.BytesIO()
    Image.new('RGB', (300, 200), 'white').save(stream, format='PNG')
    return stream.getvalue()


def upload(client, content=None, name='invoice.pdf', auth=AUTH):
    return client.post('/api/v1/documents', headers=auth,
                       files={'file': (name, content if content is not None else pdf(), 'application/octet-stream')})


def test_digital_end_to_end(client):
    response = upload(client)
    assert response.status_code == 201
    doc = response.json()
    assert client.get('/api/v1/ocr/result/'+doc['id'], headers=AUTH).status_code == 409
    process_job(doc['job_id'])
    status = client.get('/api/v1/ocr/status/'+doc['id'], headers=AUTH).json()
    assert status['stage'] == 'AI_EXTRACTION_PENDING'
    assert status['pages_processed'] == 2
    assert [e['status'] for e in status['history']] == ['UPLOADED','VALIDATING','OCR_PROCESSING','OCR_COMPLETED','AI_EXTRACTION_PENDING']
    result = client.get('/api/v1/ocr/result/'+doc['id'], headers=AUTH).json()
    assert result['page_count'] == 2
    assert '59000' in result['text']
    assert result['pages'][0]['words'][0]['bounding_box'][0] == 70
    assert result['pages'][0]['confidence'] is None
    assert result['pages'][0]['ocr_engine'] == 'pymupdf'
    assert client.get('/api/v1/ocr/metrics', headers=AUTH).json()['success_rate'] == 1


def test_tenant_isolation_and_signed_download(client):
    doc = upload(client).json()
    assert client.get('/api/v1/documents').status_code == 401
    assert client.get('/api/v1/documents', headers=OTHER).json()['items'] == []
    for endpoint in ['/ocr/status/', '/ocr/result/']:
        assert client.get('/api/v1'+endpoint+doc['id'], headers=OTHER).status_code == 404
    assert client.post('/api/v1/ocr/process', headers=OTHER, json={'document_id': doc['id']}).status_code == 404
    assert client.get('/api/v1/documents/'+doc['id']+'/download-url', headers=OTHER).status_code == 404
    url = client.get('/api/v1/documents/'+doc['id']+'/download-url', headers=AUTH).json()['url']
    assert client.get(url).content.startswith(b'%PDF-')
    assert client.get(url+'x').status_code == 403
    assert client.get(f"/api/v1/documents/{doc['id']}/file?expires=1&token=bad").status_code == 403


@pytest.mark.parametrize('content,name', [(b'', 'a.pdf'),(b'not pdf','a.pdf'),(b'hello','a.exe'),(b'%PDF-1.7 broken','a.pdf'),(png(),'a.jpg')])
def test_reject_invalid(client, content, name):
    assert upload(client, content, name).status_code == 422


def test_limits_and_password(client, monkeypatch):
    monkeypatch.setattr(settings(), 'max_file_mb', 1)
    assert upload(client, b'x'*(1024*1024+1)).status_code == 422
    monkeypatch.setattr(settings(), 'max_pages', 1)
    assert upload(client).status_code == 422
    with pymupdf.open(stream=pdf(), filetype='pdf') as doc:
        encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret')
    assert upload(client, encrypted).status_code == 422
    monkeypatch.setattr(settings(), 'max_image_pixels', 100)
    assert upload(client, png(), 'a.png').status_code == 422


def test_idempotent_upload_and_processing(client):
    content = pdf()
    first = upload(client, content).json()
    duplicate = upload(client, content).json()
    assert duplicate['duplicate'] and duplicate['id'] == first['id']
    separate = upload(client, content, auth=OTHER).json()
    assert separate['id'] != first['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(process_job, [first['job_id']]*2))
    process_job(first['job_id'])
    with Session() as db:
        assert db.get(Job, first['job_id']).attempts == 1
        assert len(list(db.scalars(select(OCRResult).where(OCRResult.document_id == first['id'])))) == 2
    assert client.post('/api/v1/ocr/process', headers=AUTH, json={'document_id':first['id']}).json()['status'] == 'completed'


def test_scanned_blank_routes_to_review(client, monkeypatch):
    monkeypatch.setattr(ocr, 'run_provider', lambda image: ([], 'test', '1', []))
    doc = upload(client, png(), 'scan.png').json()
    process_job(doc['job_id'])
    result = client.get('/api/v1/ocr/result/'+doc['id'], headers=AUTH).json()
    assert result['review_required'] and result['next_stage'] == 'HUMAN_REVIEW'
    assert 'No text detected' in ' '.join(result['warnings'])


def test_mixed_pdf_uses_ocr_for_scan(client, monkeypatch):
    word = {'text':'Scanned invoice', 'confidence':.95, 'bounding_box':[1,2,100,20], 'line_id':'1', 'granularity':'line'}
    monkeypatch.setattr(ocr, 'run_provider', lambda image: ([word], 'test', '1', []))
    with pymupdf.open(stream=pdf(1), filetype='pdf') as doc:
        page = doc.new_page()
        page.insert_image(page.rect, stream=png())
        content = doc.tobytes()
    doc = upload(client, content).json()
    process_job(doc['job_id'])
    result = client.get('/api/v1/ocr/result/'+doc['id'], headers=AUTH).json()
    assert [p['source'] for p in result['pages']] == ['digital_pdf', 'ocr']
    assert 'Scanned invoice' in result['text']


def test_retry_exhaustion_and_manual_retry(client, monkeypatch):
    def unavailable(image):
        raise ocr.ProviderUnavailable('Provider temporarily unavailable')
    monkeypatch.setattr(ocr, 'run_provider', unavailable)
    monkeypatch.setattr(settings(), 'max_attempts', 2)
    doc = upload(client, png(), 'scan.png').json()
    process_job(doc['job_id'])
    assert client.get('/api/v1/ocr/status/'+doc['id'], headers=AUTH).json()['status'] == 'retrying'
    with Session.begin() as db:
        db.execute(update(Job).where(Job.id==doc['job_id']).values(next_attempt_at=0))
    process_job(doc['job_id'])
    assert client.get('/api/v1/ocr/status/'+doc['id'], headers=AUTH).json()['status'] == 'failed'
    response = client.post('/api/v1/ocr/process', headers=AUTH, json={'document_id':doc['id']})
    assert response.json()['status'] == 'queued'
    monkeypatch.setattr(ocr, 'run_provider', lambda image: ([], 'test', '1', []))
    process_job(doc['job_id'])
    assert client.get('/api/v1/ocr/status/'+doc['id'], headers=AUTH).json()['status'] == 'completed'


def test_expired_worker_lease_is_recovered(client):
    doc = upload(client).json()
    with Session.begin() as db:
        db.execute(update(Job).where(Job.id==doc['job_id']).values(status='processing', run_token='dead-worker', lease_until=time.time()-1))
    process_job(doc['job_id'])
    assert client.get('/api/v1/ocr/status/'+doc['id'], headers=AUTH).json()['status'] == 'completed'


def test_provider_fallback_and_timeout(monkeypatch):
    import json
    import subprocess
    from pathlib import Path
    monkeypatch.setattr(settings(), 'ocr_provider', 'paddle')
    monkeypatch.setattr(settings(), 'ocr_fallback', 'tesseract')
    calls = []
    def run(command, **kwargs):
        calls.append(command[3])
        assert kwargs['timeout'] == settings().ocr_timeout_seconds
        if command[3] == 'paddle':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        Path(command[5]).write_text(json.dumps({'words':[], 'version':'test'}))
    monkeypatch.setattr(ocr.subprocess, 'run', run)
    words, provider, version, warnings = ocr.run_provider(Image.new('RGB',(30,30)))
    assert calls == ['paddle','tesseract'] and provider == 'tesseract' and warnings


def test_upload_scanner_fails_closed(client, monkeypatch):
    from app import storage
    def unavailable(content):
        raise OSError('unavailable')
    monkeypatch.setattr(storage, 'scan', unavailable)
    assert upload(client).status_code == 503
    assert client.get('/api/v1/documents', headers=AUTH).json()['total'] == 0


def test_frontend_and_openapi(client):
    response = client.get('/')
    assert response.status_code == 200 and 'Sign in to LedgerLens' in response.text
    assert "script-src 'self'" in response.headers['content-security-policy']
    assert '/api/v1/ocr/process' in client.get('/openapi.json').json()['paths']
