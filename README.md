# document-cutter

`document-cutter` 是一个独立的 FastAPI 文档切分服务，目标是替代原先依赖外部知识库切分的前半段流程，专注完成：

- 文档接入
- 文档解析
- OCR / 视觉理解回退
- 语义切分
- 结构化 chunk 输出

当前版本已经支持 `DOCX / PDF / TXT / MD / XLS / XLSX / PNG / JPG / JPEG / WEBP / BMP / TIFF`。

## 当前能力

- 支持文件上传或按 URL 拉取文档
- 支持 Markdown / Word / Excel / PDF 的结构化解析
- 支持扫描 PDF 和图片文档通过视觉模型做 OCR / 内容理解
- 支持复杂 PDF 的多策略解析：
  - PyMuPDF 版面块提取
  - pypdf 文本回退
  - 扫描件视觉 OCR 回退
- 支持按标题、段落、列表、表格等自然结构切分
- 支持同章节短块合并、超长块按句边界拆分
- 支持“规则 + 相似度 + LLM”边界增强，只对相邻块做合并判断
- 提供基础线上能力：
  - `X-Request-ID` 请求追踪
  - `/metrics` Prometheus 指标
  - 限流
  - 请求超时保护
  - 大文件限制

## 仍需持续增强的部分

- 更强的复杂表格版面恢复
- 图片区域级别的精细结构理解
- 更丰富的 OCR 后处理和召回评估
- Java 主工程接入与灰度发布

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

复制 `.env.example` 为 `.env` 后按需修改。

核心参数：

- `CUTTER_TARGET_CHUNK_CHARS`
- `CUTTER_MIN_CHUNK_CHARS`
- `CUTTER_MAX_CHUNK_CHARS`
- `CUTTER_OVERLAP_CHARS`
- `CUTTER_HTTP_TIMEOUT_SECONDS`
- `CUTTER_REQUEST_TIMEOUT_SECONDS`
- `CUTTER_MAX_UPLOAD_MB`
- `CUTTER_RATE_LIMIT_REQUESTS`
- `CUTTER_RATE_LIMIT_WINDOW_SECONDS`

LLM 边界增强：

- `CUTTER_LLM_ENABLED`
- `CUTTER_LLM_MODEL`

语义相似度增强：

- `CUTTER_SIMILARITY_ENABLED`
- `CUTTER_SIMILARITY_HIGH_THRESHOLD`
- `CUTTER_SIMILARITY_LOW_THRESHOLD`
- `CUTTER_EMBEDDING_BASE_URL`
- `CUTTER_EMBEDDING_MODEL`
- `CUTTER_EMBEDDING_TIMEOUT_SECONDS`

视觉 / OCR 后端：

- `CUTTER_OPENAI_API_KEY`
- `CUTTER_OPENAI_BASE_URL`
- `CUTTER_VISION_MODEL`
- `CUTTER_VISION_PDF_MAX_PAGES`
- `CUTTER_PDF_OCR_FALLBACK_MIN_CHARS`

### Volcengine Ark / 豆包接入示例

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
  -F "file=@sample.pdf"
```

### 2. 按 URL 切分

```bash
curl -X POST "http://127.0.0.1:8000/v1/chunk/by-url" \
  -H "Content-Type: application/json" \
  -d "{\"document_url\":\"https://example.com/demo.pdf\",\"filename\":\"demo.pdf\"}"
```

## 返回结构

每个响应都包含：

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

## 解析与切分策略

主链路按以下顺序执行：

1. 文档解析为标准 `DocumentNode`
2. 文本清洗与结构标准化
3. 按标题、段落、列表、表格形成粗粒度语义块
4. 同章节短块合并
5. 超长块按语义边界拆分
6. 先做相似度边界判断
7. 灰区再由 LLM 做边界增强
8. 输出结构化 `chunks`

### PDF 多策略解析

PDF 当前采用三层策略：

1. 优先使用 PyMuPDF 提取版面文本块和表格
2. 若失败或内容过少，回退到 `pypdf` 文本提取
3. 若仍接近无文本，则使用视觉模型对扫描 PDF 做 OCR

同时会做基础去噪：

- 去除重复页眉页脚
- 去除页码
- 保留页码来源信息

### 图片和扫描件

图片文档与扫描 PDF 会走视觉模型，返回标准化节点：

- `title`
- `paragraph`
- `table`
- `list`

这样后续切分和普通文本文档保持同一套主链路。

### 边界增强策略

边界增强采用三段式策略：

1. 规则先过滤：不同章节、标题块、表格块、超长组合直接不合并
2. 相似度判定：
   - 高于高阈值直接合并
   - 低于低阈值直接保留
3. 灰区才调用 LLM 决定是否合并

这样可以把大模型调用控制在必要范围内，同时保留稳定的主链路。

## 线上能力说明

### 请求追踪

- 所有请求都会返回 `X-Request-ID`
- 日志里会带 `request_id`

### 监控

- `GET /metrics` 暴露 Prometheus 指标
- 当前包含：
  - `document_cutter_http_requests_total`
  - `document_cutter_http_request_duration_seconds`

### 限流

- 默认对业务接口开启基础限流
- `/health` 和 `/metrics` 不限流

### 超时

- URL 下载超时由 `CUTTER_HTTP_TIMEOUT_SECONDS` 控制
- 整体请求处理超时由 `CUTTER_REQUEST_TIMEOUT_SECONDS` 控制
- 超时返回 `504`

### 大文件策略

- 上传文件大小由 `CUTTER_MAX_UPLOAD_MB` 控制
- 超过限制返回 `413`

## 与 Java Model-Engine 对接

Java 侧建议替换点在：

- `KnowledgePipelineOrchestrator.uploadAndAggregateChunks(...)`

推荐对接方式：

1. Java 调用 Python `POST /v1/chunk/by-url`
2. Python 返回结构化 `chunks`
3. Java 将 `chunks` 映射为现有 `MergedChunk`
4. 继续复用 `runModelTasks(...)`、决策流和校验流

字段映射建议：

- `chunk.text -> mergedContent`
- `chunk.source_node_ids -> referenceIds`
- `chunk.section_path / metadata -> 扩展元信息`

更详细说明见 [docs/integration.md](/D:/bailing/document-cutter/docs/integration.md)。

## 测试

```bash
python -m pytest
```
