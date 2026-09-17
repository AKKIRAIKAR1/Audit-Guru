import hashlib
import logging
import time
import uuid

from sqlalchemy import and_, delete, or_, select, update

from app import storage
from app.config import settings
from app.db import Document, Job, OCRResult, Session
from app.ocr import pages
from app.validation import InvalidDocument, validate

logger = logging.getLogger(__name__)


def event(status):
    return {'status': status, 'at': time.time()}


def due_condition(now):
    return or_(and_(Job.status.in_(['queued', 'retrying']), Job.next_attempt_at <= now),
               and_(Job.status == 'processing', Job.lease_until < now))


def due_jobs():
    with Session() as db:
        return list(db.scalars(select(Job.id).where(due_condition(time.time())).limit(100)))


def process_job(job_id):
    cfg, token, started = settings(), uuid.uuid4().hex, time.monotonic()
    with Session.begin() as db:
        claimed = db.execute(update(Job).where(Job.id == job_id, due_condition(time.time())).values(
            status='processing', run_token=token, lease_until=time.time() + cfg.job_lease_seconds,
            attempts=Job.attempts + 1, pages_processed=0, error=None))
        if not claimed.rowcount:
            return
        job = db.get(Job, job_id)
        doc = db.get(Document, job.document_id)
        doc_id, key, name, media_type, digest = doc.id, doc.storage_path, doc.file_name, doc.file_type, doc.sha256
        doc.status = 'VALIDATING'
        job.history = job.history + [event('VALIDATING')]
        attempts = job.attempts
    try:
        if attempts > cfg.max_attempts:
            raise InvalidDocument('Worker repeatedly stopped before completing this job. Retry after checking worker health.')
        content = storage.get(key)
        validate(content, name)
        if hashlib.sha256(content).hexdigest() != digest:
            raise InvalidDocument('Stored document integrity check failed.')
        with Session.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.run_token != token:
                return
            db.get(Document, doc_id).status = 'OCR_PROCESSING'
            job.history = job.history + [event('OCR_PROCESSING')]
        results = []
        for page in pages(content, media_type):
            results.append(page)
            with Session.begin() as db:
                updated = db.execute(update(Job).where(Job.id == job_id, Job.run_token == token).values(
                    pages_processed=len(results), lease_until=time.time() + cfg.job_lease_seconds))
                if not updated.rowcount:
                    return
        with Session.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.run_token != token:
                return
            db.execute(delete(OCRResult).where(OCRResult.document_id == doc_id))
            for result in results:
                db.add(OCRResult(document_id=doc_id, page_number=result['page_number'],
                                 extracted_text=result['text'], ocr_engine=result['ocr_engine'],
                                 ocr_version=result['ocr_version'], processing_time=result['processing_time'], payload=result))
            scores = [w['confidence'] for p in results for w in p['words'] if w['confidence'] is not None]
            job.confidence = sum(scores) / len(scores) if scores else None
            job.review_required = any(p['review_required'] for p in results)
            job.warnings = [f"Page {p['page_number']}: {warning}" for p in results for warning in p['warnings']]
            job.processing_time = time.monotonic() - started
            job.status, job.completed_at, job.lease_until = 'completed', time.time(), 0
            final = 'HUMAN_REVIEW' if job.review_required else 'AI_EXTRACTION_PENDING'
            db.get(Document, doc_id).status = final
            job.history = job.history + [event('OCR_COMPLETED'), event(final)]
    except Exception as exc:
        logger.exception('OCR job %s failed on attempt %s', job_id, attempts)
        with Session.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.run_token != token:
                return
            retry = not isinstance(exc, InvalidDocument) and attempts < cfg.max_attempts
            job.status = 'retrying' if retry else 'failed'
            job.error = str(exc) if isinstance(exc, (InvalidDocument, RuntimeError)) else 'Processing failed. Check worker logs or retry.'
            job.next_attempt_at = time.time() + min(300, 5 * 2 ** attempts)
            job.lease_until = 0
            job.processing_time = time.monotonic() - started
            db.get(Document, doc_id).status = 'OCR_RETRY_PENDING' if retry else 'OCR_FAILED'
            job.history = job.history + [event('OCR_RETRY_PENDING' if retry else 'OCR_FAILED')]
