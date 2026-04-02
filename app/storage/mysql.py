from __future__ import annotations

"""MySQL 连接与建库建表初始化。"""

from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import settings


CREATE_DATABASE_SQL = f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"

CREATE_DOCUMENTS_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(36) PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    total_chunks INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_CHUNKS_SQL = """
CREATE TABLE IF NOT EXISTS document_chunks (
    id VARCHAR(36) PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    chunk_index INT NOT NULL,
    chunk_type VARCHAR(32) NOT NULL,
    section_path JSON NULL,
    page_no JSON NULL,
    preview_text TEXT NOT NULL,
    full_text MEDIUMTEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_document_chunks_document_id FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    INDEX idx_document_chunks_document_id (document_id),
    INDEX idx_document_chunks_chunk_index (chunk_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


class MySQLDatabase:
    """集中管理 MySQL 连接和初始化。"""

    def _connect(self, include_database: bool) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database if include_database else None,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def initialize(self) -> None:
        with self._connect(include_database=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_DATABASE_SQL)
            connection.commit()

        with self._connect(include_database=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_DOCUMENTS_SQL)
                cursor.execute(CREATE_CHUNKS_SQL)
            connection.commit()

    @contextmanager
    def connection(self):
        connection = self._connect(include_database=True)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


database = MySQLDatabase()
