"""Run `python -m app.worker` locally, or use the Celery app in Docker."""
import logging
import time

from celery import Celery
from app.config import settings
from app.db import init_db
from app.jobs import due_jobs, process_job

celery_app = Celery('invoice_ocr', broker=settings().redis_url)
celery_app.conf.update(task_ignore_result=True, worker_prefetch_multiplier=1,
                       task_acks_late=True, task_reject_on_worker_lost=True,
                       task_soft_time_limit=720, task_time_limit=780,
                       beat_schedule={'dispatch-ocr-outbox': {'task': 'ocr.dispatch', 'schedule': 5.0}})


@celery_app.task(name='ocr.process')
def process(job_id):
    process_job(job_id)


@celery_app.task(name='ocr.dispatch')
def dispatch():
    for job_id in due_jobs():
        process.apply_async(args=[job_id], expires=30)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_db()
    print('OCR worker running. Press Ctrl+C to stop.', flush=True)
    try:
        while True:
            for job_id in due_jobs():
                process_job(job_id)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
