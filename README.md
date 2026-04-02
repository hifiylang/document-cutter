# document-cutter

`document-cutter` 是一个面向 RAG 与知识抽取场景的生产级文档理解与语义切分服务。它支持多格式文档解析、OCR、PDF 图片区域理解、Token 优先切分，以及规则、Embedding、LLM 结合的边界增强能力，能够把复杂文档稳定转换为高质量、可检索的知识块。

## 当前能力

- 支持 `DOCX / PDF / TXT / MD / XLS / XLSX / PNG / JPG / JPEG / WEBP / BMP / TIF / TIFF`
- 支持 Word、Markdown、Excel、PDF 的结构化解析
- 支持扫描 PDF 与图片文档的视觉 OCR 回退
- 支持 PDF 页面内图片区域裁剪、视觉解析与按页内位置回挂
- 支持 token-first 切分、短块合并、递归拆分、overlap
- 支持规则过滤、Embedding 相似度、LLM 灰区裁决的混合边界增强
- 支持请求级覆盖模型与 embedding 配置
- 支持限流、超时、请求追踪、Prometheus 指标

## 主流程

1. 文档解析为标准 `DocumentNode`
2. 文本清洗与结构标准化
3. 按标题、段落、列表、表格、sheet 做结构切分
4. 按 token 预算合并短块
5. 对超长块做递归语义拆分
6. 对相邻块执行“规则 + Embedding + LLM 灰区兜底”的边界增强
7. 输出标准 `ChunkResponse`

## 项目结构

```text
app/
  api/                 HTTP 路由
  core/                配置、错误、日志、指标、限流
  models/              Pydantic 模型
  services/
    parsers/           文档解析器拆包
    boundary_*         边界增强拆包
    pipeline.py        主流水线
    selection.py       统一模型 / embedding 选择器
    token_counter.py   token 计数
    text_chunker.py    切分内核
    serializer.py      响应序列化
tests/
```

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

### 切分预算

- `CUTTER_TARGET_CHUNK_TOKENS`
- `CUTTER_MIN_CHUNK_TOKENS`
- `CUTTER_MAX_CHUNK_TOKENS`
- `CUTTER_OVERLAP_RATIO`
- `CUTTER_OVERLAP_TOKENS`
- `CUTTER_TOKEN_COUNTER_PROVIDER`
- `CUTTER_TOKEN_COUNTER_ENDPOINT`
- `CUTTER_TOKEN_COUNTER_TIMEOUT_SECONDS`

### 相似度增强

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

### 服务治理

- `CUTTER_HTTP_TIMEOUT_SECONDS`
- `CUTTER_REQUEST_TIMEOUT_SECONDS`
- `CUTTER_MAX_UPLOAD_MB`
- `CUTTER_RATE_LIMIT_REQUESTS`
- `CUTTER_RATE_LIMIT_WINDOW_SECONDS`

### OCR / PDF

- `CUTTER_VISION_PDF_MAX_PAGES`
- `CUTTER_PDF_OCR_FALLBACK_MIN_CHARS`

## 模型角色

当前代码把模型职责统一成四类：

- `text_model`
  - 通用文本模型
  - 预留给复杂文本抽取、总结、结构化任务
- `flash_model`
  - 轻量文本模型
  - 当前主要用于相邻 chunk 的边界 merge / keep 裁决
- `vision_model`
  - OCR、图片理解、PDF 图片区域解析
- `embedding_model`
  - 相邻文本块的语义相似度计算

## 请求级覆盖

上传接口支持额外传入：

- `text_model`
- `flash_model`
- `vision_model`
- `embedding_base_url`
- `embedding_model`
- `embedding_api_key`

URL 接口支持通过 `options` 传入同名字段。

响应里会返回本次实际生效的选择：

- `metadata.selected_options.text_model`
- `metadata.selected_options.flash_model`
- `metadata.selected_options.vision_model`
- `metadata.selected_options.embedding_base_url`
- `metadata.selected_options.embedding_model`

## 接口示例

### 1. 上传文档切分

```bash
curl -X POST "http://127.0.0.1:8000/v1/chunk/by-upload" ^
  -F "file=@sample.pdf" ^
  -F "max_chunk_tokens=450" ^
  -F "overlap_tokens=24" ^
  -F "flash_model=<your-flash-model>" ^
  -F "embedding_base_url=http://<your-embedding-service>/v1/embeddings" ^
  -F "embedding_model=<your-embedding-model>"
```

### 2. 按 URL 切分

```bash
curl -X POST "http://127.0.0.1:8000/v1/chunk/by-url" ^
  -H "Content-Type: application/json" ^
  -d "{\"document_url\":\"https://example.com/demo.pdf\",\"filename\":\"demo.pdf\",\"options\":{\"max_chunk_tokens\":450,\"overlap_tokens\":24,\"vision_model\":\"<your-vision-model>\",\"embedding_base_url\":\"http://<your-embedding-service>/v1/embeddings\",\"embedding_model\":\"<your-embedding-model>\"}}"
```

## 返回结构

响应包含：

- `document_id`
- `filename`
- `total_nodes`
- `total_chunks`
- `chunks`
- `metadata.selected_options`

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

说明：

- `char_count` 现在只作为输出统计信息，不参与切分预算
- 真正的预算控制完全由 token 配置决定
- `offsets` 和 `source_spans` 用于原文回溯、高亮和调试

## 测试

```bash
python -m compileall app tests
python -m pytest
```

当前重构后测试已覆盖：

- Office / PDF / Excel 解析
- 扫描 PDF OCR 回退
- PDF 图片区域解析的核心路径
- token-first 长文本拆分
- 边界增强高分 / 低分 / 灰区 / 回退
- 请求级模型和 embedding 覆盖
- API 上传、URL、限流、超时、错误码

## Java 对接

后续 Java 侧可以在 `KnowledgePipelineOrchestrator.uploadAndAggregateChunks(...)` 位置切到：

- `POST /v1/chunk/by-url`

Java 收到结果后，直接把 `chunks` 转成现有 `MergedChunk` 即可继续复用后续知识抽取链路。
