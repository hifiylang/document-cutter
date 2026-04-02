# document-cutter

`document-cutter` 是一个独立的 FastAPI 文档切分服务，目标是替代原先依赖外部知识库做文档切分的前半段流程，专注完成：

- 文档接入
- 多格式解析
- OCR / 视觉理解回退
- 结构化语义切分
- 面向知识抽取 / RAG 的 chunk 输出

当前版本支持：

- `DOCX`
- `PDF`
- `TXT`
- `MD`
- `XLS`
- `XLSX`
- `PNG / JPG / JPEG / WEBP / BMP / TIF / TIFF`

## 当前设计

当前主链路已经升级为 token-first 的切分方案，不再以字符长度作为主预算控制。

完整流程：

1. 文档解析为标准 `DocumentNode`
2. 文本清洗与结构标准化
3. 按标题、段落、列表、表格、sheet 做结构切分
4. 按 token 预算合并短块
5. 对超长块做递归语义拆分
6. 对相邻块执行规则 + embedding + LLM 灰区兜底的边界增强
7. 输出结构化 `ChunkResponse`

## 当前能力

- 支持文件上传或按 URL 拉取文档
- 支持 Markdown / Word / Excel / PDF 的结构化解析
- 支持扫描 PDF 和图片文档通过视觉模型做 OCR / 内容理解
- 支持复杂 PDF 的多策略解析：
  - `PyMuPDF` 版面块提取
  - `pypdf` 文本回退
  - 扫描件视觉 OCR 回退
- 支持按标题、段落、列表、表格等自然结构切分
- 支持 token-aware 短块合并和超长块递归拆分
- 支持 chunk `offsets / source_spans / parser_strategy / token_count`
- 支持“规则 + 相似度 + LLM 灰区兜底”的边界增强
- 提供基础线上能力：
  - `X-Request-ID` 请求追踪
  - `/metrics` Prometheus 指标
  - 限流
  - 请求超时保护
  - 大文件限制

## 安装与启动

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

启动后默认可访问：

- `GET /health`
- `GET /metrics`
- `POST /v1/chunk/by-upload`
- `POST /v1/chunk/by-url`

## 环境变量

复制 [`.env.example`](/D:/bailing/document-cutter/.env.example) 为 `.env` 后按需修改。

### 切分与预算

- `CUTTER_TARGET_CHUNK_TOKENS`
- `CUTTER_MIN_CHUNK_TOKENS`
- `CUTTER_MAX_CHUNK_TOKENS`
- `CUTTER_OVERLAP_RATIO`
- `CUTTER_OVERLAP_TOKENS`
- `CUTTER_TOKEN_COUNTER_PROVIDER`
- `CUTTER_TOKEN_COUNTER_ENDPOINT`
- `CUTTER_TOKEN_COUNTER_TIMEOUT_SECONDS`

### 请求与服务治理

- `CUTTER_HTTP_TIMEOUT_SECONDS`
- `CUTTER_REQUEST_TIMEOUT_SECONDS`
- `CUTTER_MAX_UPLOAD_MB`
- `CUTTER_RATE_LIMIT_REQUESTS`
- `CUTTER_RATE_LIMIT_WINDOW_SECONDS`

### 相似度增强

- `CUTTER_SIMILARITY_ENABLED`
- `CUTTER_SIMILARITY_HIGH_THRESHOLD`
- `CUTTER_SIMILARITY_LOW_THRESHOLD`
- `CUTTER_EMBEDDING_BASE_URL`
- `CUTTER_EMBEDDING_MODEL`
- `CUTTER_EMBEDDING_TIMEOUT_SECONDS`

### LLM 边界增强

- `CUTTER_LLM_ENABLED`
- `CUTTER_LLM_MODEL`

### 视觉 / OCR

- `CUTTER_OPENAI_API_KEY`
- `CUTTER_OPENAI_BASE_URL`
- `CUTTER_VISION_MODEL`
- `CUTTER_VISION_PDF_MAX_PAGES`
- `CUTTER_PDF_OCR_FALLBACK_MIN_CHARS`

## Volcengine Ark / 豆包接入示例

如果你使用火山引擎 Ark 的 OpenAI 兼容接口，可以这样配置：

```env
CUTTER_OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
CUTTER_OPENAI_API_KEY=你的 API Key
CUTTER_VISION_MODEL=你的视觉 endpoint id
CUTTER_LLM_MODEL=你的文本 endpoint id
```

如果同一个 endpoint 同时支持视觉和文本，也可以把 `CUTTER_VISION_MODEL` 和 `CUTTER_LLM_MODEL` 配成同一个 endpoint id。

## 接口示例

### 1. 上传文档切分

```bash
curl -X POST "http://127.0.0.1:8000/v1/chunk/by-upload" \
  -F "file=@sample.pdf" \
  -F "max_chunk_tokens=450" \
  -F "overlap_tokens=24"
```

### 2. 按 URL 切分

```bash
curl -X POST "http://127.0.0.1:8000/v1/chunk/by-url" \
  -H "Content-Type: application/json" \
  -d "{\"document_url\":\"https://example.com/demo.pdf\",\"filename\":\"demo.pdf\",\"options\":{\"max_chunk_tokens\":450,\"overlap_tokens\":24}}"
```

## 返回结构

每个响应包含：

- `document_id`
- `filename`
- `total_nodes`
- `total_chunks`
- `chunks`

每个 `chunk` 至少包含：

- `chunk_id`
- `text`
- `char_count`
- `token_estimate`
- `source_node_ids`
- `section_path`
- `metadata.chunk_type`
- `metadata.title`
- `metadata.page_no`
- `metadata.parser_type`
- `metadata.strategy_version`
- `metadata.modality`
- `metadata.sheet_name`
- `metadata.parser_strategy`
- `metadata.token_count`
- `metadata.offsets`
- `metadata.source_spans`
- `metadata.merge_strategy`
- `metadata.similarity_score`

## 解析与切分策略

### 结构优先

主链路优先保留自然结构边界：

- 标题
- 段落
- 列表
- 表格
- Excel sheet

标题、表格、sheet 都会被视为强边界，不会被后续边界增强随意跨过去。

### Token-first 预算治理

当前预算控制已经改为 token-aware：

- 短块合并看 token
- 超长块拆分看 token
- overlap 看 token
- 边界增强是否允许合并也看 token

字符数只作为输出统计信息保留，不再承担主切分职责。

### 递归 splitter

对于单个超长正文块，内部会按更自然的分隔符优先级递归拆分：

1. 多换行
2. 单换行
3. tab
4. 空白
5. 句末符号
6. 子句分隔符
7. 词连接符
8. 最后才硬切

拆开后会重新按 token 预算装箱，避免切得过碎。

### 边界增强策略

边界增强采用三段式策略：

1. 规则先过滤
   - 不同章节不合并
   - 标题块不合并
   - 表格块不合并
   - 超出 token 上限不合并
2. 相似度判定
   - 高于高阈值直接合并
   - 低于低阈值直接保留
3. 灰区才调用 LLM
   - 只判断相邻块是否应合并
   - 不参与全文重切

## PDF 解析策略

PDF 当前采用三层策略：

1. 优先使用 `PyMuPDF` 做版面块提取和表格抽取
2. 若内容不足，回退到 `pypdf`
3. 若仍接近无文本，则走视觉 OCR

同时会做基础去噪：

- 去掉重复页眉页脚
- 去掉页码
- 过滤和表格 bbox 重叠的普通文本块

## Excel 处理策略

Excel 当前按 `sheet` 做章节边界：

- 先输出 sheet 标题节点
- 再输出该 sheet 的表格节点

这样后续 `section_path` 会天然带上 sheet 信息，便于检索和回溯。

## 线上能力

### 请求追踪

- 所有请求都会返回 `X-Request-ID`
- 日志会带 `request_id`

### 指标

- `GET /metrics` 暴露 Prometheus 指标
- 当前包含：
  - `document_cutter_http_requests_total`
  - `document_cutter_http_request_duration_seconds`
  - `document_cutter_boundary_decisions_total`
  - `document_cutter_external_calls_total`
  - `document_cutter_external_call_duration_seconds`
  - `document_cutter_token_count_calls_total`
  - `document_cutter_token_count_duration_seconds`
  - `document_cutter_overlap_hits_total`
  - `document_cutter_recursive_split_depth`

### 限流 / 超时 / 大文件

- `/health` 和 `/metrics` 不限流
- URL 下载超时由 `CUTTER_HTTP_TIMEOUT_SECONDS` 控制
- 整体处理超时由 `CUTTER_REQUEST_TIMEOUT_SECONDS` 控制
- 文件大小由 `CUTTER_MAX_UPLOAD_MB` 控制

## 与 Java Model-Engine 对接

Java 侧推荐替换点：

- `KnowledgePipelineOrchestrator.uploadAndAggregateChunks(...)`

推荐接入方式：

1. Java 调用 Python `POST /v1/chunk/by-url`
2. Python 返回结构化 `chunks`
3. Java 把 `chunks` 映射为现有 `MergedChunk`
4. 继续复用 `runModelTasks(...)`、决策流和校验流

更详细说明见 [integration.md](/D:/bailing/document-cutter/docs/integration.md)。

## 验证

基础验证命令：

```bash
python -m compileall app
python -c "from app.main import app; print(app.title)"
```
