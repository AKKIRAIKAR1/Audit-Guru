import hashlib
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app import storage
from app.auth import router as auth_router, session_tenant
from app.config import settings
from app.db import Document, Job, OCRResult, Session, init_db
from app.jobs import event
from app.invoice_summary import invoice_summary
from app.validation import InvalidDocument, validate

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='Invoice OCR Processing', version='1.0.0', lifespan=lifespan)
app.include_router(auth_router)
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')


@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    if request.url.path == '/':
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"
    return response


def tenant(request: Request, x_api_key: str = Header(default='')):
    organization = session_tenant(request)
    if organization:
        return organization
    for key, organization in settings().api_keys.items():
        if hmac.compare_digest(key.encode(), x_api_key.encode()):
            return organization
    raise HTTPException(401, 'Please sign in.')


def owned(db, document_id, organization):
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.organization_id == organization))
    if not doc:
        raise HTTPException(404, 'Document not found.')
    return doc


def document_json(doc):
    return {key: getattr(doc, key) for key in ('id', 'file_name', 'file_type', 'file_size', 'page_count', 'status', 'created_at')}


def new_job(doc_id):
    return Job(document_id=doc_id, history=[event('UPLOADED')])


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / 'static' / 'index.html')


@app.get('/health')
def health():
    try:
        with Session() as db:
            db.execute(text('SELECT 1'))
    except Exception:
        raise HTTPException(503, 'Database unavailable.')
    return {'status': 'ok'}


def save_upload(content, filename, organization):
    try:
        media_type, page_count = validate(content, filename)
    except InvalidDocument as exc:
        raise HTTPException(422, str(exc))
    try:
        storage.scan(content)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except OSError:
        raise HTTPException(503, 'Malware scanning is temporarily unavailable.')
    digest = hashlib.sha256(content).hexdigest()
    with Session() as db:
        existing = db.scalar(select(Document).where(Document.organization_id == organization, Document.sha256 == digest))
        if existing:
            job = db.scalar(select(Job).where(Job.document_id == existing.id))
            return {**document_json(existing), 'document_id': existing.id, 'job_id': job.id, 'duplicate': True}
    doc_id = uuid.uuid4().hex
    # Tenant identifiers never become filesystem paths supplied by the caller.
    key = f'{hashlib.sha256(organization.encode()).hexdigest()}/{doc_id}{Path(filename).suffix.lower()}'
    try:
        storage.put(key, content, media_type)
    except Exception:
        logger.exception('Document storage unavailable')
        raise HTTPException(503, 'Document storage is temporarily unavailable.')
    try:
        with Session.begin() as db:
            doc = Document(id=doc_id, organization_id=organization, file_name=filename,
                           file_type=media_type, file_size=len(content), storage_path=key,
                           page_count=page_count, sha256=digest, status='UPLOADED')
            db.add(doc)
            db.flush()
            job = new_job(doc.id)
            db.add(job)
            db.flush()
            output = {**document_json(doc), 'document_id': doc.id, 'job_id': job.id, 'duplicate': False}
        return output
    except IntegrityError:
        storage.delete(key)
        with Session() as db:
            doc = db.scalar(select(Document).where(Document.organization_id == organization, Document.sha256 == digest))
            if doc is None:
                raise
            job = db.scalar(select(Job).where(Job.document_id == doc.id))
            return {**document_json(doc), 'document_id': doc.id, 'job_id': job.id, 'duplicate': True}
    except Exception:
        storage.delete(key)
        raise


@app.post('/api/v1/documents', status_code=201)
async def upload(file: UploadFile = File(...), organization: str = Depends(tenant)):
    try:
        content = await file.read(settings().max_file_mb * 1024 * 1024 + 1)
        filename = (file.filename or 'invoice').replace('\\', '/').split('/')[-1][:255]
        return await run_in_threadpool(save_upload, content, filename, organization)
    finally:
        await file.close()


@app.get('/api/v1/documents')
def documents(organization: str = Depends(tenant), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with Session() as db:
        query = select(Document).where(Document.organization_id == organization)
        total = db.scalar(select(func.count()).select_from(Document).where(Document.organization_id == organization))
        rows = list(db.scalars(query.order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)))
        completed_ids = [doc.id for doc in rows if doc.status in ('AI_EXTRACTION_PENDING', 'HUMAN_REVIEW', 'OCR_COMPLETED')]
        texts = {}
        if completed_ids:
            for doc_id, payload in db.execute(select(OCRResult.document_id, OCRResult.payload)
                    .where(OCRResult.document_id.in_(completed_ids)).order_by(OCRResult.page_number)):
                texts.setdefault(doc_id, []).append(payload)
        return {'items': [{**document_json(doc), 'invoice_summary': invoice_summary('', texts[doc.id])
                          if doc.id in texts else None} for doc in rows], 'total': total}


class ProcessRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=32)


@app.post('/api/v1/ocr/process', status_code=202)
def start(request: ProcessRequest, organization: str = Depends(tenant)):
    with Session.begin() as db:
        doc = owned(db, request.document_id, organization)
        job = db.scalar(select(Job).where(Job.document_id == doc.id))
        # Only failed jobs can be explicitly retried; in-flight/completed calls are idempotent.
        result = db.execute(update(Job).where(Job.id == job.id, Job.status == 'failed').values(
            status='queued', attempts=0, next_attempt_at=time.time(), run_token=None, error=None,
            pages_processed=0, confidence=None, completed_at=None, processing_time=None,
            review_required=False, warnings=[]))
        if result.rowcount:
            doc.status = 'UPLOADED'
            job.history = job.history + [event('UPLOADED')]
        db.refresh(job)
        return {'job_id': job.id, 'status': job.status}


@app.get('/api/v1/ocr/status/{document_id}')
def status(document_id: str, organization: str = Depends(tenant)):
    with Session() as db:
        doc = owned(db, document_id, organization)
        job = db.scalar(select(Job).where(Job.document_id == doc.id))
        return {'document_id': doc.id, 'job_id': job.id, 'status': job.status, 'stage': doc.status,
                'page_count': doc.page_count, 'pages_processed': job.pages_processed,
                'confidence': job.confidence, 'processing_time': job.processing_time,
                'attempts': job.attempts, 'error': job.error, 'review_required': job.review_required,
                'warnings': job.warnings, 'history': job.history,
                'next_attempt_at': job.next_attempt_at if job.status == 'retrying' else None}


@app.get('/api/v1/ocr/result/{document_id}')
def result(document_id: str, organization: str = Depends(tenant)):
    with Session() as db:
        doc = owned(db, document_id, organization)
        job = db.scalar(select(Job).where(Job.document_id == doc.id))
        if job.status != 'completed':
            raise HTTPException(409, 'OCR result is not ready.')
        rows = list(db.scalars(select(OCRResult).where(OCRResult.document_id == doc.id).order_by(OCRResult.page_number)))
        return {'schema_version': '1.0', 'document_id': doc.id, 'text': '\n\n'.join(row.extracted_text for row in rows),
                'page_count': len(rows), 'pages': [row.payload for row in rows], 'confidence': job.confidence,
                'review_required': job.review_required, 'next_stage': doc.status,
                'processing_time': job.processing_time, 'warnings': job.warnings,
                'invoice_summary': invoice_summary('', [row.payload for row in rows])}


def signature(doc_id, organization, expires):
    payload = f'{doc_id}:{organization}:{expires}'.encode()
    return hmac.new(settings().signing_secret.encode(), payload, hashlib.sha256).hexdigest()


@app.get('/api/v1/documents/{document_id}/download-url')
def download_url(document_id: str, organization: str = Depends(tenant)):
    with Session() as db:
        owned(db, document_id, organization)
    expires = int(time.time()) + 300
    return {'url': f'/api/v1/documents/{document_id}/file?expires={expires}&token={signature(document_id, organization, expires)}',
            'expires_at': expires}


@app.get('/api/v1/documents/{document_id}/file')
def download(document_id: str, expires: int, token: str):
    if expires < time.time() or expires > time.time() + 301:
        raise HTTPException(403, 'Download link expired.')
    with Session() as db:
        doc = db.get(Document, document_id)
        if not doc or not hmac.compare_digest(token, signature(document_id, doc.organization_id, expires)):
            raise HTTPException(403, 'Invalid download link.')
        return Response(storage.get(doc.storage_path), media_type=doc.file_type,
                        headers={'Content-Disposition': f'attachment; filename="invoice{Path(doc.file_name).suffix.lower()}"'})


@app.get('/api/v1/ocr/metrics')
def metrics(organization: str = Depends(tenant)):
    with Session() as db:
        rows = list(db.scalars(select(Job).join(Document).where(Document.organization_id == organization)))
        completed = [j for j in rows if j.status == 'completed']
        failed = [j for j in rows if j.status == 'failed']
        times = [j.processing_time for j in completed if j.processing_time is not None]
        confidence = [j.confidence for j in completed if j.confidence is not None]
        return {'total_documents': len(rows), 'completed': len(completed), 'failed': len(failed),
                'queue_size': sum(j.status in ('queued', 'retrying') for j in rows),
                'processing': sum(j.status == 'processing' for j in rows),
                'manual_review': sum(j.review_required for j in completed),
                'success_rate': len(completed) / (len(completed) + len(failed)) if completed or failed else None,
                'average_processing_time': sum(times) / len(times) if times else None,
                'average_confidence': sum(confidence) / len(confidence) if confidence else None}
