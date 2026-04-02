# document-cutter

`document-cutter` 是一个面向 RAG、知识库构建与知识抽取场景的文档理解与语义切分服务。它支持多格式文档解析、OCR、PDF 页面图片区域提取、Token 优先切分，以及规则、Embedding、LLM 结合的边界增强能力，能够把复杂文档稳定转换为适合检索和下游知识处理的高质量 Chunk。

## 核心能力

- 支持 `DOC / DOCX / PDF / TXT / MD / XLS / XLSX / PNG / JPG / JPEG / WEBP / BMP / TIF / TIFF`
- 支持 Word、Markdown、Excel、PDF 的结构化解析
- 支持扫描 PDF 和图片文档的视觉 OCR 回退
- 支持 PDF 页面内图片区域裁剪、视觉解析和按页内位置回挂
- 支持标题、段落、列表、表格、sheet 等自然结构切分
- 支持 Token 优先的短块合并、超长块递归拆分和 overlap
- 支持规则过滤、Embedding 相似度、LLM 灰区裁决组成的边界增强
- 支持切分结果落库、分页查询和按需获取 chunk 详情

## 适用场景

- RAG 知识库文档预处理
- FAQ、手册、制度、技术文档切分入库
- 扫描件、图文混排 PDF 的文本提取和语义切分
- 需要分页浏览 chunk、按需取全文的大文档处理场景

## 整体流程

1. 接收上传文件或远程 URL 文档
2. 将原始文档解析为统一的 `DocumentNode`
3. 对节点做清洗、去噪、层级修正和表格归一
4. 按标题、段落、列表、表格、sheet 等自然结构做第一轮切分
5. 按 Token 预算做短块合并和超长块递归拆分
6. 对可疑边界执行规则过滤、Embedding 相似度判断和 LLM 灰区裁决
7. 将切分结果写入数据库，并通过摘要、分页列表、详情接口对外提供

## 标题处理策略

当前实现中，标题不会再作为常规独立 Chunk 输出。系统会默认把标题挂接到其后的正文、列表或表格块中，使正文 Chunk 自身携带章节语境。

这样做的目的不是简化结构，而是避免 RAG 召回时出现“标题命中了、正文没命中”或“正文命中了但缺少章节上下文”的问题。`section_path` 仍然保留，但它只作为辅助结构信息，不能替代正文中的显式标题语境。

## 项目结构

```text
app/
  api/                 HTTP 路由
  core/                配置、错误、日志、指标、限流
  models/              Pydantic 模型
  services/
    parsers/           各类文档解析器
    pipeline.py        主流程编排
    text_chunker.py    切分内核
    segmenter.py       结构切分
    merger.py          短块合并
    splitter.py        超长块递归拆分
    boundary_*.py      边界增强
    selection.py       模型与 embedding 统一选择
    serializer.py      结果序列化
    document_store.py  文档与 chunk 落库
  storage/             MySQL 连接与初始化
tests/
```

## 本地启动

### 1. 安装依赖

```bash
python -m pip install -r requirements.txt
```

### 2. 启动服务

```bash
uvicorn app.main:app --reload
```

启动后可访问：

- `GET /health`
- `GET /metrics`

## Docker 启动

项目已内置 `docker-compose.yml`，可直接启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

停止服务：

```bash
docker compose down
```

### Docker 与 MySQL 说明

当前 Docker 配置已通过 `host.docker.internal` 连接宿主机 MySQL，因此容器内不会再把 `localhost` 误认为容器自身。默认适用于本机 MySQL 部署场景。

## 环境变量

复制 [`.env.example`](/D:/bailing/document-cutter/.env.example) 为 `.env` 后按需修改。

### 基础服务配置

- `CUTTER_APP_NAME`
- `CUTTER_DEBUG`
- `CUTTER_HTTP_TIMEOUT_SECONDS`
- `CUTTER_REQUEST_TIMEOUT_SECONDS`
- `CUTTER_MAX_UPLOAD_MB`
- `CUTTER_RATE_LIMIT_REQUESTS`
- `CUTTER_RATE_LIMIT_WINDOW_SECONDS`
- `CUTTER_DOWNLOAD_SIZE_GUARD_FACTOR`
- `CUTTER_DOWNLOAD_ALLOWED_HOSTS`

### Token 切分配置

- `CUTTER_TARGET_CHUNK_TOKENS`
- `CUTTER_MIN_CHUNK_TOKENS`
- `CUTTER_MAX_CHUNK_TOKENS`
- `CUTTER_OVERLAP_RATIO`
- `CUTTER_OVERLAP_TOKENS`
- `CUTTER_TOKEN_COUNTER_PROVIDER`
- `CUTTER_TOKEN_COUNTER_ENDPOINT`
- `CUTTER_TOKEN_COUNTER_TIMEOUT_SECONDS`

### Embedding 配置

- `CUTTER_SIMILARITY_ENABLED`
- `CUTTER_SIMILARITY_HIGH_THRESHOLD`
- `CUTTER_SIMILARITY_LOW_THRESHOLD`
- `CUTTER_EMBEDDING_BASE_URL`
- `CUTTER_EMBEDDING_API_KEY`
- `CUTTER_EMBEDDING_MODEL`
- `CUTTER_EMBEDDING_TIMEOUT_SECONDS`

### 模型配置

- `CUTTER_LLM_ENABLED`
- `CUTTER_TEXT_MODEL`
- `CUTTER_FLASH_MODEL`
- `CUTTER_VISION_MODEL`
- `CUTTER_OPENAI_API_KEY`
- `CUTTER_OPENAI_BASE_URL`

### OCR / PDF 配置

- `CUTTER_VISION_PDF_MAX_PAGES`
- `CUTTER_PDF_OCR_FALLBACK_MIN_CHARS`

### MySQL 配置

- `CUTTER_MYSQL_HOST`
- `CUTTER_MYSQL_PORT`
- `CUTTER_MYSQL_USER`
- `CUTTER_MYSQL_PASSWORD`
- `CUTTER_MYSQL_DATABASE`

## 模型职责

当前模型角色分为三类：

- `text_model`
  - 预留给通用文本理解任务
- `flash_model`
  - 轻量文本模型
  - 当前主要用于边界增强阶段的 `merge / keep` 裁决
- `vision_model`
  - 用于 OCR、图片理解、PDF 图片区域解析

Embedding 不属于生成模型，而是单独用于相邻块语义相似度计算。

## 接口说明

### 1. 上传文档并切分

`POST /v1/chunk/by-upload`

功能：
- 上传文件
- 完成切分
- 结果写入数据库
- 返回文档摘要

表单参数：

- `file`
- `target_chunk_tokens` 可选
- `min_chunk_tokens` 可选
- `max_chunk_tokens` 可选
- `overlap_ratio` 可选
- `overlap_tokens` 可选

返回示例：

```json
{
  "document_id": "doc_xxx",
  "filename": "sample.pdf",
  "status": "completed",
  "total_chunks": 18
}
```

### 2. 按 URL 拉取文档并切分

`POST /v1/chunk/by-url`

请求体示例：

```json
{
  "document_url": "https://example.com/demo.pdf",
  "filename": "demo.pdf",
  "options": {
    "max_chunk_tokens": 450,
    "overlap_tokens": 24
  }
}
```

返回值与上传接口一致，都是文档摘要。

### 3. 查询文档摘要

`GET /v1/documents/{document_id}`

返回示例：

```json
{
  "document_id": "doc_xxx",
  "filename": "sample.pdf",
  "status": "completed",
  "total_chunks": 18
}
```

### 4. 分页查询 chunk 列表

`GET /v1/documents/{document_id}/chunks?page=1&page_size=20`

功能：
- 返回预览列表
- 不返回全文，避免大文档一次性加载过长内容

返回示例：

```json
{
  "document_id": "doc_xxx",
  "filename": "sample.pdf",
  "total_chunks": 18,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "chunk_id": "chunk_xxx",
      "preview_text": "这是一个 chunk 的预览内容……",
      "section_path": ["项目经历", "文档解析"],
      "metadata": {
        "chunk_type": "paragraph",
        "page_no": [1]
      }
    }
  ]
}
```

### 5. 查询单个 chunk 详情

`GET /v1/chunks/{chunk_id}`

返回示例：

```json
{
  "chunk_id": "chunk_xxx",
  "document_id": "doc_xxx",
  "text": "这里是该 chunk 的完整正文内容。",
  "section_path": ["项目经历", "文档解析"],
  "metadata": {
    "chunk_type": "paragraph",
    "page_no": [1]
  }
}
```

## 最小调用示例

### 上传切分

```bash
curl -X POST "http://127.0.0.1:8000/v1/chunk/by-upload" ^
  -F "file=@sample.pdf" ^
  -F "max_chunk_tokens=450" ^
  -F "overlap_tokens=24"
```

### 查询 chunk 列表

```bash
curl "http://127.0.0.1:8000/v1/documents/<document_id>/chunks?page=1&page_size=20"
```

## 常见说明

### 为什么大文档不直接返回全部 chunk？

因为大文档一次性返回完整内容会导致响应过长、前端加载压力大，也不利于后续按需查看。当前设计是：

- 切分结果先入库
- 上传接口只返回文档摘要
- 列表接口返回预览
- 详情接口按需返回完整正文

### 为什么标题不再单独切成 chunk？

因为标题和正文分离后，会导致 RAG 检索时语义上下文缺失。当前实现会把标题默认挂到后续正文、列表或表格块中，让正文自身携带章节语境。

### 扫描 PDF 为什么会走 OCR？

如果 PDF 无法直接提取足够文本，系统会自动回退到视觉 OCR，以保证扫描件和图片型文档也能被切分。

### Docker 下为什么要特别处理 MySQL 地址？

因为容器内的 `localhost` 指向容器自身，不是宿主机。当前 `docker-compose.yml` 已经把 MySQL 主机覆盖为 `host.docker.internal`，用于连接宿主机上的数据库。

## Java 对接说明

如果后续需要从 Java 服务侧接入，建议在现有文档切分入口处直接调用：

- `POST /v1/chunk/by-url`

然后通过 `document_id` 再分页读取 chunk 列表或按需读取详情。这样可以避免一次性把整份文档的所有 chunk 全量拉回主服务。
