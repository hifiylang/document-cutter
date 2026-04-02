# document-cutter

`document-cutter` 是一个独立的 FastAPI 文档切分服务，目标是替代外部文档切分前半链路，专注完成：

- 多格式文档接入
- 文档解析与结构化抽取
- PDF / 图片 OCR 与视觉理解
- token-first 语义切分
- 面向知识抽取 / RAG 的标准 chunk 输出

当前支持：

- `DOCX`
- `PDF`
- `TXT`
- `MD`
- `XLS`
- `XLSX`
- `PNG / JPG / JPEG / WEBP / BMP / TIF / TIFF`

## 核心流程

主链路已经完全切到 token-first：

1. 文档解析为标准 `DocumentNode`
2. 文本清洗与结构标准化
3. 按标题、段落、列表、表格、sheet 做结构切分
4. 按 token 预算合并短块
5. 对超长块做递归语义拆分
6. 对相邻块执行“规则 + embedding + LLM 灰区兜底”的边界增强
7. 输出标准 `ChunkResponse`

## 当前能力

- 支持上传文件和按 URL 拉取文档
- 支持 Word、Markdown、Excel、PDF 的结构化解析
- 支持扫描 PDF 和图片文档通过视觉模型做 OCR / 内容理解
- 支持复杂 PDF 的多策略解析：
  - `PyMuPDF` 版面块提取
  - `pypdf` 文本回退
  - 扫描件整页 OCR 回退
- 支持 PDF 页面内图片区域提取：
  - 检测图片区域
  - 按 `bbox` 裁剪局部图
  - 调用视觉模型提取图片中的文字 / 表格 / 列表
  - 按页面原始位置回挂到节点流
- 支持 token-aware 合并、递归 splitter、offsets / source spans 输出
- 支持基础线上能力：
  - `X-Request-ID`
  - `/metrics`
  - 限流
  - 超时保护
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

### 相似度增强

- `CUTTER_SIMILARITY_ENABLED`
- `CUTTER_SIMILARITY_HIGH_THRESHOLD`
- `CUTTER_SIMILARITY_LOW_THRESHOLD`
- `CUTTER_EMBEDDING_BASE_URL`
- `CUTTER_EMBEDDING_MODEL`
- `CUTTER_EMBEDDING_TIMEOUT_SECONDS`

### 服务治理

- `CUTTER_HTTP_TIMEOUT_SECONDS`
- `CUTTER_REQUEST_TIMEOUT_SECONDS`
- `CUTTER_MAX_UPLOAD_MB`
- `CUTTER_RATE_LIMIT_REQUESTS`
- `CUTTER_RATE_LIMIT_WINDOW_SECONDS`

### 模型配置

- `CUTTER_LLM_ENABLED`
- `CUTTER_TEXT_MODEL`
- `CUTTER_FLASH_MODEL`
- `CUTTER_VISION_MODEL`
- `CUTTER_OPENAI_API_KEY`
- `CUTTER_OPENAI_BASE_URL`

### OCR 与 PDF

- `CUTTER_VISION_PDF_MAX_PAGES`
- `CUTTER_PDF_OCR_FALLBACK_MIN_CHARS`

## 模型角色

当前代码把模型职责统一成三类：

- `text_model`
  - 通用文本模型
  - 预留给后续复杂文本抽取、总结、结构化任务
- `vision_model`
  - 负责 OCR、图片理解、PDF 图片区域解析
- `flash_model`
  - 负责简单高频文本任务
  - 当前主要用于相邻 chunk 的边界 merge / keep 裁决

当前绑定关系：

- `VisualDocumentAnalyzer` 使用 `CUTTER_VISION_MODEL`
- `LlmBoundaryRefiner` 优先使用 `CUTTER_FLASH_MODEL`
- 如果没有单独配置 `CUTTER_FLASH_MODEL`，边界裁决会自动回退到 `CUTTER_TEXT_MODEL`

## 模型调用封装

模型调用已经统一收口到内部封装层：

- [model_client.py](/D:/bailing/document-cutter/app/services/model_client.py)

调用方只需要传：

- `model`
- `enable_thinking`

当前默认策略：

- 简单任务默认 `enable_thinking=False`
- flash 小模型默认不开 thinking / reasoning
- 复杂视觉任务继续使用视觉模型

## Volcengine Ark 接入示例

如果使用火山引擎 Ark 的 OpenAI 兼容接口，可以这样配置：

```env
CUTTER_OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
CUTTER_OPENAI_API_KEY=你的 API Key
CUTTER_TEXT_MODEL=你的通用文本 endpoint id
CUTTER_FLASH_MODEL=你的轻量文本 endpoint id
CUTTER_VISION_MODEL=你的视觉 endpoint id
```

推荐分工：

- `CUTTER_FLASH_MODEL`：简单边界裁决、小文本任务
- `CUTTER_VISION_MODEL`：OCR、图片理解、PDF 图片区域提取
- `CUTTER_TEXT_MODEL`：后续复杂文本任务

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

响应包含：

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

说明：

- `char_count` 现在只是输出统计信息，不参与切分预算
- 真正的预算控制完全由 token 配置决定

## 切分策略

### 结构优先

主链路优先保留自然结构边界：

- 标题
- 段落
- 列表
- 表格
- Excel sheet

标题、表格、sheet 都是强边界，不会被后续边界增强随意跨越。

### Token-first 预算治理

当前切分完全基于 token 预算：

- 短块合并看 token
- 超长块拆分看 token
- overlap 看 token
- 边界增强是否允许合并也看 token

### 递归 splitter

单个超长正文块内部会按更自然的分隔符优先级递归拆分：

1. 多换行
2. 单换行
3. tab
4. 空白
5. 句末符号
6. 子句分隔符
7. 词连接符
8. 最后才硬切

### 边界增强

边界增强采用三段式：

1. 规则先过滤
   - 不同章节不合并
   - 标题块不合并
   - 表格块不合并
   - 超过 token 上限不合并
2. 相似度判断
   - 高于高阈值直接合并
   - 低于低阈值直接保留
3. 灰区才调 LLM
   - 只判断相邻块是否应合并
   - 不参与全文重切

## PDF 解析策略

PDF 当前采用四层策略：

1. `PyMuPDF` 提取正文块和表格块
2. 检测页面内图片区域并做局部视觉解析
3. 把图片内容按页内位置回挂到节点流
4. 如果文本仍然极少，再回退到整页 OCR

同时会做基础去噪：

- 去重复页眉页脚
- 去页码
- 过滤和表格 `bbox` 重叠的普通文本块

## 与 Java Model-Engine 对接

推荐替换点：

- `KnowledgePipelineOrchestrator.uploadAndAggregateChunks(...)`

推荐接入方式：

1. Java 调用 Python `POST /v1/chunk/by-url`
2. Python 返回结构化 `chunks`
3. Java 把 `chunks` 映射为现有 `MergedChunk`
4. 继续复用 `runModelTasks(...)`、决策流和校验流

更详细说明见 [integration.md](/D:/bailing/document-cutter/docs/integration.md)。
