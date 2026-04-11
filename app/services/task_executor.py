from __future__ import annotations

"""任务级并行执行器，单任务内部保持串行。"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid
from typing import Iterator

from app.core.config import settings
from app.models.schemas import ChunkOptions, DocumentTask, TaskEvent
from app.services.document_store import store
from app.services.pipeline import DocumentChunkPipeline


class DocumentTaskExecutor:
    """用线程池并发执行多个文档任务，每个任务内部仍走串行 pipeline。"""

    def __init__(self) -> None:
        self.pipeline = DocumentChunkPipeline()
        self._lock = threading.RLock()
        self._tasks: dict[str, DocumentTask] = {}
        self._events: dict[str, list[TaskEvent]] = defaultdict(list)
        self._conditions: dict[str, threading.Condition] = {}
        self._executor = ThreadPoolExecutor(max_workers=settings.task_workers, thread_name_prefix="document-task")

    def submit_upload_task(self, file_bytes: bytes, filename: str, options: ChunkOptions | None = None) -> DocumentTask:
        return self._submit("upload", file_bytes=file_bytes, filename=filename, options=options)

    def submit_url_task(self, document_url: str, filename: str, options: ChunkOptions | None = None) -> DocumentTask:
        return self._submit("url", document_url=document_url, filename=filename, options=options)

    def get_task(self, task_id: str) -> DocumentTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    def stream_task(self, task_id: str) -> Iterator[TaskEvent]:
        condition = self._get_condition(task_id)
        offset = 0
        while True:
            with condition:
                condition.wait_for(
                    lambda: offset < len(self._events.get(task_id, [])) or self._is_terminal(task_id),
                    timeout=1.0,
                )
                events = list(self._events.get(task_id, []))

            while offset < len(events):
                event = events[offset]
                offset += 1
                yield event

            if self._is_terminal(task_id):
                break

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._events.clear()
            self._conditions.clear()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _submit(self, kind: str, **kwargs) -> DocumentTask:
        now = time.time()
        task = DocumentTask(
            task_id=str(uuid.uuid4()),
            document_id=str(uuid.uuid4()),
            filename=kwargs["filename"],
            status="queued",
            progress_message="queued",
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._conditions[task.task_id] = threading.Condition()
        self._append_event(
            task.task_id,
            TaskEvent(
                event="task_queued",
                task_id=task.task_id,
                document_id=task.document_id,
                filename=task.filename,
                status="queued",
                progress_message="queued",
            ),
        )
        self._executor.submit(self._run_task, kind, task.task_id, kwargs)
        return task

    def _run_task(self, kind: str, task_id: str, payload: dict) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        self._update_task(task_id, status="processing", progress_message="processing")
        try:
            if kind == "upload":
                response = self._run_upload_pipeline(task.document_id, payload["file_bytes"], payload["filename"], payload.get("options"))
            else:
                response = self._run_url_pipeline(task.document_id, payload["document_url"], payload["filename"], payload.get("options"))
            store.save(response)
            self._update_task(task_id, status="completed", progress_message="completed")
        except Exception as exc:
            self._update_task(task_id, status="failed", progress_message="failed", error_message=str(exc))

    def _run_upload_pipeline(
        self,
        document_id: str,
        file_bytes: bytes,
        filename: str,
        options: ChunkOptions | None,
    ):
        try:
            return self.pipeline.chunk_bytes(file_bytes, filename, options, document_id=document_id)
        except TypeError as exc:
            if "document_id" not in str(exc):
                raise
            response = self.pipeline.chunk_bytes(file_bytes, filename, options)
            response.document_id = document_id
            return response

    def _run_url_pipeline(
        self,
        document_id: str,
        document_url: str,
        filename: str,
        options: ChunkOptions | None,
    ):
        try:
            return self.pipeline.chunk_url(document_url, filename, options, document_id=document_id)
        except TypeError as exc:
            if "document_id" not in str(exc):
                raise
            response = self.pipeline.chunk_url(document_url, filename, options)
            response.document_id = document_id
            return response

    def _update_task(
        self,
        task_id: str,
        *,
        status: str,
        progress_message: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._lock:
            task = self._tasks[task_id].model_copy(deep=True)
            task.status = status  # type: ignore[assignment]
            task.progress_message = progress_message
            task.error_message = error_message
            task.updated_at = time.time()
            self._tasks[task_id] = task

        event_name = {
            "processing": "task_processing",
            "completed": "task_completed",
            "failed": "task_failed",
        }.get(status)
        if event_name:
            self._append_event(
                task_id,
                TaskEvent(
                    event=event_name,  # type: ignore[arg-type]
                    task_id=task.task_id,
                    document_id=task.document_id,
                    filename=task.filename,
                    status=task.status,
                    progress_message=task.progress_message,
                    error_message=task.error_message,
                ),
            )

    def _append_event(self, task_id: str, event: TaskEvent) -> None:
        condition = self._get_condition(task_id)
        with condition:
            self._events[task_id].append(event)
            condition.notify_all()

    def _get_condition(self, task_id: str) -> threading.Condition:
        with self._lock:
            condition = self._conditions.get(task_id)
            if condition is None:
                condition = threading.Condition()
                self._conditions[task_id] = condition
            return condition

    def _is_terminal(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        return task is not None and task.status in {"completed", "failed"}


task_executor = DocumentTaskExecutor()
