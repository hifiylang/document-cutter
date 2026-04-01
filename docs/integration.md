# Java 接入说明

## 接入目标

用 `document-cutter` 替代 Java 主工程里原有“文档上传到外部知识库并拉取切片”的前半段流程，只替代：

- 文档解析
- 文档 OCR / 视觉理解
- 文档切分

不改动后续：

- QA 生成
- 文档分类
- 内容校验
- 结构化存储

## Java 侧替换点

当前主要替换点在：

- `KnowledgePipelineOrchestrator.uploadAndAggregateChunks(...)`

原流程：

1. 上传文档到外部知识库
2. 轮询等待解析完成
3. 拉取 chunk
4. 聚合成 `MergedChunk`

替换后建议流程：

1. Java 调用 Python `POST /v1/chunk/by-url`
2. 获取 `chunks`
3. 映射成 `MergedChunk`
4. 继续执行 `runModelTasks(...)`

## 字段映射建议

- `chunk.text -> mergedContent`
- `chunk.source_node_ids -> referenceIds`
- `chunk.section_path -> 扩展 metadata.sectionPath`
- `chunk.metadata.chunk_type -> 扩展 metadata.chunkType`
- `chunk.metadata.page_no -> 扩展 metadata.pageNo`

## 失败策略

第一版建议：

- Python 服务成功：直接使用 Python 返回结果
- Python 服务失败：由 Java 配置决定回退旧链路或直接失败

## 推荐接入方式

Java 可继续沿用现有 `documentUrl`，直接调用：

```http
POST /v1/chunk/by-url
Content-Type: application/json

{
  "document_url": "https://example.com/demo.pdf",
  "filename": "demo.pdf"
}
```

## 响应示例

```json
{
  "document_id": "bca0f7d7-caad-43a2-b67b-b34aa462c3cb",
  "filename": "demo.pdf",
  "total_nodes": 12,
  "total_chunks": 4,
  "chunks": [
    {
      "chunk_id": "7190e19d-06cf-4e30-a7dd-09b88142930b",
      "text": "Chapter 1 Overview ...",
      "char_count": 320,
      "token_estimate": 80,
      "source_node_ids": ["n1", "n2"],
      "section_path": ["Chapter 1"],
      "metadata": {
        "chunk_type": "paragraph",
        "title": "Chapter 1",
        "page_no": [1],
        "parser_type": "pdf",
        "strategy_version": "v1",
        "modality": null,
        "sheet_name": null
      }
    }
  ]
}
```
