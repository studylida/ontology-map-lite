# server/src/ontology_map/services/task_queue.py
"""인메모리 FIFO 비동기 문서 추출 대기열 매니저."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple
import uuid

from sqlalchemy.orm import Session

from ontology_map.db.session import get_engine
from ontology_map.schemas import IntakePayload
from ontology_map.services.document_parser import extract_document_text
from ontology_map.services.entity_resolution import get_existing_entity_names
from ontology_map.services.ingestion_agent import extract_ontology_from_text

TaskStatus = Literal["pending", "processing", "completed", "failed"]


@dataclass
class ExtractionTask:
    id: str
    source_type: str
    title: str
    status: TaskStatus = "pending"
    result: Optional[IntakePayload] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def to_summary_dict(self) -> Dict[str, Any]:
        """목록 조회용 요약 딕셔너리."""
        return {
            "id": self.id,
            "source_type": self.source_type,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "node_count": len(self.result.nodes) if self.result else 0,
            "edge_count": len(self.result.edges) if self.result else 0,
        }


class TaskQueueManager:
    """단일 워커(Concurrency=1) 기반 FIFO 인메모리 태스크 큐."""

    def __init__(self) -> None:
        self.tasks: Dict[str, ExtractionTask] = {}
        self.queue: asyncio.Queue[Tuple[str, str, bytes | str, Optional[str]]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None

    def _ensure_worker(self) -> None:
        """백그라운드 워커가 돌고 있지 않다면 시작합니다."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        """대기열에서 작업을 하나씩 꺼내 순차 처리하는 워커 루프."""
        while True:
            task_id, source_type, raw_data, filename_or_url = await self.queue.get()
            task = self.tasks.get(task_id)
            if not task:
                self.queue.task_done()
                continue

            task.status = "processing"

            try:
                # 1. 문서 텍스트 추출 (동기 파싱)
                title, content = extract_document_text(source_type, raw_data, filename_or_url)
                task.title = title

                # 2. 기존 지식베이스의 주요 노드명 조회 (지식 교차 Weaving용)
                existing_entities: list[str] = []
                try:
                    with Session(get_engine()) as session:
                        existing_entities = get_existing_entity_names(session, limit=60)
                except Exception:
                    pass

                payload: IntakePayload = await asyncio.to_thread(
                    extract_ontology_from_text,
                    title,
                    content,
                    source_type,
                    existing_entities,
                )

                task.result = payload
                task.status = "completed"
                task.completed_at = datetime.now(timezone.utc)
            except Exception as exc:
                task.status = "failed"
                task.error = str(exc)
                task.completed_at = datetime.now(timezone.utc)
            finally:
                self.queue.task_done()

    async def enqueue(
        self,
        source_type: str,
        raw_data: bytes | str,
        filename_or_url: Optional[str] = None,
    ) -> str:
        """새 추출 작업을 대기열에 등록하고 고유 Task ID를 즉시 반환합니다."""
        task_id = uuid.uuid4().hex[:12]
        title_placeholder = filename_or_url or f"{source_type.upper()} 작업"

        task = ExtractionTask(
            id=task_id,
            source_type=source_type,
            title=title_placeholder,
            status="pending",
        )
        self.tasks[task_id] = task

        self._ensure_worker()
        await self.queue.put((task_id, source_type, raw_data, filename_or_url))
        return task_id

    def get_task(self, task_id: str) -> Optional[ExtractionTask]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> List[ExtractionTask]:
        return sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)

    def dismiss_task(self, task_id: str) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False


# 전역 싱글톤 인스턴스
task_queue_manager = TaskQueueManager()
