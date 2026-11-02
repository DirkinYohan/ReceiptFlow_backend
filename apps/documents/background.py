from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=getattr(settings, 'DOCUMENT_BACKGROUND_WORKERS', 3),
                thread_name_prefix='receiptflow-document',
            )
        return _executor


def _process_in_thread(document_id: int) -> None:
    from .tasks import process_document_task

    try:
        process_document_task.apply(kwargs={'document_id': document_id})
    except Exception:
        logger.exception('Background processing failed for document %s', document_id)
    finally:
        connections.close_all()


def schedule_document_processing(document_id: int):
    if getattr(settings, 'DOCUMENT_BACKGROUND_THREADS', False):
        future: Future = _get_executor().submit(_process_in_thread, document_id)
        return future
    from .tasks import process_document_task

    return process_document_task.delay(document_id=document_id)
