from __future__ import annotations

"""文档切分结果的 MySQL 持久化与查询。"""

import json
from typing import Any

from app.models.schemas import Chunk, ChunkMetadata, ChunkResponse
from app.storage import database


class DocumentStore:
    """负责保存文档、chunk，以及分页和详情查询。"""

    def save(self, response: ChunkResponse) -> dict[str, Any]:
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO documents (id, filename, status, total_chunks) VALUES (%s, %s, %s, %s)",
                    (response.document_id, response.filename, "completed", response.total_chunks),
                )
                for index, chunk in enumerate(response.chunks, start=1):
                    cursor.execute(
                        """
                        INSERT INTO document_chunks (
                            id, document_id, chunk_index, chunk_type, section_path, page_no, preview_text, full_text
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            chunk.chunk_id,
                            response.document_id,
                            index,
                            chunk.metadata.chunk_type,
                            json.dumps(chunk.section_path, ensure_ascii=False),
                            json.dumps(chunk.metadata.page_no, ensure_ascii=False),
                            self._preview_text(chunk.text),
                            chunk.text,
                        ),
                    )
        return {
            "document_id": response.document_id,
            "filename": response.filename,
            "status": "completed",
            "total_chunks": response.total_chunks,
        }

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id AS document_id, filename, status, total_chunks FROM documents WHERE id = %s",
                    (document_id,),
                )
                return cursor.fetchone()

    def list_chunks(self, document_id: str, page: int, page_size: int) -> dict[str, Any] | None:
        document = self.get_document(document_id)
        if not document:
            return None

        offset = (page - 1) * page_size
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id AS chunk_id, chunk_type, section_path, page_no, preview_text
                    FROM document_chunks
                    WHERE document_id = %s
                    ORDER BY chunk_index ASC
                    LIMIT %s OFFSET %s
                    """,
                    (document_id, page_size, offset),
                )
                items = cursor.fetchall()

        return {
            "document_id": document["document_id"],
            "filename": document["filename"],
            "total_chunks": document["total_chunks"],
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "chunk_id": item["chunk_id"],
                    "preview_text": item["preview_text"],
                    "section_path": json.loads(item["section_path"] or "[]"),
                    "metadata": {
                        "chunk_type": item["chunk_type"],
                        "page_no": json.loads(item["page_no"] or "[]"),
                    },
                }
                for item in items
            ],
        }

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id AS chunk_id, document_id, chunk_type, section_path, page_no, full_text
                    FROM document_chunks
                    WHERE id = %s
                    """,
                    (chunk_id,),
                )
                row = cursor.fetchone()

        if not row:
            return None

        return {
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "text": row["full_text"],
            "section_path": json.loads(row["section_path"] or "[]"),
            "metadata": {
                "chunk_type": row["chunk_type"],
                "page_no": json.loads(row["page_no"] or "[]"),
            },
        }

    def clear_all(self) -> None:
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM document_chunks")
                cursor.execute("DELETE FROM documents")

    def _preview_text(self, text: str, limit: int = 200) -> str:
        normalized = text.replace("\n", " ").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "..."


store = DocumentStore()
