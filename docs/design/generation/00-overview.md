# 生成阶段总体设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-GEN-000 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 生成阶段总体架构、流程编排、接口与 SSE 协议、数据模型、降级、性能目标 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义 RAG 生成阶段的总体架构、流程编排、接口契约、会话数据模型、降级策略和性能目标，作为查询理解、上下文组装、答案生成、引用校验和可观测五个模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 对话式问答接口（流式 SSE 和一次性 JSON）；
- 生成阶段的流程编排和超时控制；
- 会话历史、消息、引用和反馈的数据模型；
- 查询改写、检索调用、上下文组装、生成、引用校验的模块契约；
- 生成阶段的降级矩阵、性能目标、日志指标和测试策略。

本文档不覆盖：

- 文档索引写入（见 RAG-IDX 文档集）；
- 检索服务内部实现（见 RAG-RET 文档集）；
- 前端对话 UI 和交互细节；
- 用户认证和权限体系（当前缺失，见 6.3 节）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| 生成阶段 | 从用户问题到带引用答案的完整处理链路。 |
| 查询理解 / 改写 | 把用户问题转换成适合检索的查询，并抽取过滤条件。 |
| 证据 / Evidence | 从检索阶段拿回的父块及其命中子块片段。 |
| 上下文组装 | 在 token 预算内选择证据、组织历史、生成 Prompt。 |
| 引用 / Citation | 答案中 `[n]` 编号与证据的映射关系。 |
| 引用校验 | 检查答案中的引用是否都来自证据、是否有无依据陈述。 |
| 拒答 | 证据不足时明确说明无法回答，而不是编造。 |
| SSE | Server-Sent Events，服务端流式推送协议。 |
| 首 token 延迟 | 从收到请求到模型输出第一个 token 的时间。 |

### 1.4 设计依据

- [生成阶段文档集说明](./README.md)
- [检索阶段总体设计](../retrieval/00-overview.md)
- [RRF 融合详细设计](../retrieval/03-rrf-fusion.md)
- [索引阶段总体设计](../indexing/00-overview.md)
- 阿里云 DashScope 官方文档

### 1.5 设计目标

- 一次请求完成"改写 → 检索 → 组装 → 生成 → 校验"全链路；
- 默认流式输出，首 token 尽量快，用户可感知进度；
- 答案必须有依据，引用可解析、可跳转、可追溯；
- 查询改写、rerank、引用校验都是可降级环节，不阻塞主链路；
- 会话可追溯：每个问题、改写结果、证据、引用、耗时和 token 用量都落库；
- 可评估：定义离线指标和线上反馈闭环，支撑 Prompt 和模型迭代；
- 与检索阶段接口解耦，检索阶段变更不影响生成阶段协议。

## 2. 非目标

- 不做智能体（Agent）编排和工具调用；
- 不做多模态输入输出（图片、语音、视频）；
- 不做答案的自动执行（如下单、发邮件）；
- 不做用户权限和租户隔离（当前缺失，生产前必须补齐）；
- 不做模型训练和微调；
- 不做跨会话长期记忆和个性化画像；
- 不做答案的多语言翻译。

## 3. 总体架构

```text
┌───────────────────────────────────────────────────────────────────────┐
│                            调用方（对话前端 / 服务）                   │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ POST /api/chat/answer
                               ▼
┌───────────────────────────────────────────────────────────────────────┐
│                         GenerationService（编排）                      │
│                                                                       │
│  1. 会话与历史加载（MySQL）                                            │
│         │                                                             │
│         ▼                                                             │
│  2. 查询理解与改写（LLM，可回退）  ──► rewritten_query / sub_queries   │
│         │                            / filters / keywords             │
│         ▼                                                             │
│  3. 检索调用（RetrievalService，多查询并行） ──► Top-k 父块 + 子块片段  │
│         │                                                             │
│         ▼                                                             │
│  4. 上下文组装（证据选择 / token 预算 / 引用编号 / Prompt）             │
│         │                                                             │
│         ▼                                                             │
│  5. 答案生成（LLM，流式）  ──► delta 流                                │
│         │                                                             │
│         ▼                                                             │
│  6. 引用解析与校验（引用映射 / 覆盖率 / 修复或标注）                    │
│         │                                                             │
│         ▼                                                             │
│  7. 消息持久化与指标上报（MySQL / 日志 / Prometheus）                   │
│         │                                                             │
└─────────┼─────────────────────────────────────────────────────────────┘
          │ SSE: meta / delta / citations / done / error
          ▼
       调用方
```

组件职责：

| 组件 | 职责 | 详细设计 |
| --- | --- | --- |
| `GenerationService` | 流程编排、超时、降级、持久化 | 本文档 |
| `QueryUnderstander` | 查询改写、拆解、过滤抽取 | RAG-GEN-010 |
| `RetrievalClient` | 调用检索服务，支持多查询 | RAG-GEN-010、RAG-RET-000 |
| `ContextAssembler` | 证据选择、Prompt 组装、历史管理 | RAG-GEN-020 |
| `AnswerGenerator` | 大模型调用、流式输出、重试 | RAG-GEN-030 |
| `CitationVerifier` | 引用解析、校验、修复建议 | RAG-GEN-040 |
| `ConversationStore` | 会话和消息持久化 | 本文档 |
| `GenerationMetrics` | 日志、指标、评估数据 | RAG-GEN-050 |

## 4. 生成流程

### 4.1 主流程

```text
1. 校验请求（message 非空、长度限制、stream 参数）
2. 加载会话和历史（新建会话或读取已有会话）
3. 查询理解与改写
   - 判断是否需要改写（快速路径可跳过）
   - 输出 rewritten_query、sub_queries、filters、keywords
   - 失败时回退原始问题，标记 degraded
4. 调用检索
   - 主查询 + 最多 3 个子查询
   - 每路调用 RetrievalService，多查询结果按 RRF 合并
   - 检索完全失败返回 503；单路降级继续
5. 上下文组装
   - 证据排序、去重、token 预算分配、引用编号
   - 无证据时进入拒答分支
6. 答案生成
   - 流式调用大模型，推送 delta
   - 客户端断开时取消上游调用
7. 引用解析与校验
   - 解析 [n]、建立引用映射、检查覆盖率
   - 校验失败按策略修复或标注
8. 持久化与返回
   - 写入消息、引用、耗时、usage、状态
   - 推送 citations 和 done 事件，或返回完整 JSON
```

### 4.2 超时预算

| 阶段 | 超时 | 失败处理 |
| --- | --- | --- |
| 会话加载 | 1s | 返回 503 `GEN_MYSQL_UNAVAILABLE` |
| 查询改写 | 8s | 回退原始问题，`degraded=true` |
| 检索（含多查询） | 30s | 单路降级；完全失败 503 |
| 上下文组装 | 100ms | 内部错误，返回 500 |
| 答案生成 | 首 token 15s，总 120s | 重试或降级，见 8 节 |
| 引用校验 | 5s | 标记未校验，不阻塞返回 |
| 消息持久化 | 1s | 记录错误，不影响本次响应 |
| 总预算 | 150s | 超出返回 `GEN_TIMEOUT` |

### 4.3 状态机

```text
消息（conversation_messages.status）：
PENDING -> GENERATING -> COMPLETED
                     └─> REFUSED       （无证据，明确拒答）
                     └─> PARTIAL       （流式中断，保留已生成内容）
                     └─> FAILED        （生成失败）
                     └─> CANCELLED     （客户端主动断开）
                     └─> FILTERED      （内容安全过滤）
```

## 5. 接口契约

### 5.1 请求

```json
{
  "conversation_id": null,
  "message": "那它呢？",
  "stream": true,
  "document_ids": ["a1b2c3d4e5f6..."],
  "file_types": ["pdf"],
  "top_k": 10,
  "include_evidence": true
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `conversation_id` | string | 否 | 无 | 为空时新建会话 |
| `message` | string | 是 | 无 | 用户问题，长度 ≤ `GEN_MAX_QUESTION_CHARS` |
| `stream` | bool | 否 | `true` | 是否流式返回 |
| `document_ids` | string[] | 否 | 无 | 限定检索文档 |
| `file_types` | string[] | 否 | 无 | 限定文件类型 |
| `top_k` | int | 否 | `10` | 检索父块数，范围 1-50 |
| `include_evidence` | bool | 否 | `true` | 是否在响应中返回证据列表 |

### 5.2 非流式响应

```json
{
  "conversation_id": "conv-...",
  "message_id": "msg-...",
  "answer": "华东区收入占比为 42%，主要来自……[1][2]",
  "rewritten_query": "2026 年华东区收入结构是什么？",
  "sub_queries": [],
  "citations": [
    {
      "index": 1,
      "parent_id": "p1a2...",
      "document_id": "a1b2...",
      "document_name": "2026 年财务报告.pdf",
      "file_type": "pdf",
      "locator": "page:10-12",
      "page_start": 10,
      "page_end": 12,
      "heading_path": "第三章 > 3.2 收入结构"
    }
  ],
  "evidence_count": 5,
  "citation_verified": true,
  "citation_warnings": [],
  "usage": {"input_tokens": 3500, "output_tokens": 420},
  "timing_ms": {
    "conversation": 5,
    "rewrite": 320,
    "retrieval": 260,
    "assemble": 8,
    "generation": 3200,
    "citation": 15,
    "total": 3808
  },
  "degraded": false,
  "degraded_reasons": []
}
```

### 5.3 SSE 事件协议

流式模式下按顺序推送五类事件：

```text
event: meta
data: {"conversation_id":"conv-...","message_id":"msg-...","rewritten_query":"...","evidence_count":5}

event: delta
data: {"content":"华东区"}

event: delta
data: {"content":"收入占比为 42%……"}

event: citations
data: {"citations":[{"index":1,"parent_id":"p1a2...","document_name":"2026 年财务报告.pdf","locator":"page:10-12"}],"citation_verified":true,"citation_warnings":[]}

event: done
data: {"finish_reason":"stop","usage":{"input_tokens":3500,"output_tokens":420},"timing_ms":{...}}
```

异常时推送：

```text
event: error
data: {"code":"GEN_LLM_TIMEOUT","message":"生成超时，请重试"}
```

约定：

- `meta` 必须第一个推送，`done` 或 `error` 必须最后一个；
- `delta` 可推送 0 到 N 次；
- `citations` 在 `done` 之前推送，拒答时 `citations` 为空数组；
- SSE 心跳每 15 秒发送一次注释行 `: keep-alive`，防止代理断连；
- 断流后不支持断点续传，前端按消息 ID 重新拉取完整消息。

### 5.4 错误码

| 错误码 | HTTP | 说明 | 可重试 |
| --- | --- | --- | --- |
| `GEN_INVALID_REQUEST` | 400 | 参数校验失败 | 否 |
| `GEN_CONVERSATION_NOT_FOUND` | 404 | 会话不存在 | 否 |
| `GEN_MYSQL_UNAVAILABLE` | 503 | 会话存储不可用 | 是 |
| `GEN_REWRITE_FAILED` | 200 | 改写失败已回退，`degraded=true` | 是 |
| `GEN_RETRIEVAL_UNAVAILABLE` | 503 | 检索完全不可用 | 是 |
| `GEN_LLM_UNAVAILABLE` | 503 | 大模型不可用且备模型失败 | 是 |
| `GEN_LLM_TIMEOUT` | 200/504 | 生成超时，流中通过 error 事件返回 | 是 |
| `GEN_CONTENT_FILTERED` | 200 | 内容被安全过滤，消息状态 `FILTERED` | 否 |
| `GEN_CITATION_CHECK_FAILED` | 200 | 引用校验失败，已标注警告 | 是 |
| `GEN_TIMEOUT` | 504 | 超过总预算 | 是 |
| `GEN_INTERNAL_ERROR` | 500 | 未预期错误 | 否 |

单点降级（改写、引用校验、rerank）不返回 5xx，通过 `degraded` 和 `degraded_reasons` 表达。

## 6. 会话与多轮

### 6.1 会话模型

- 一个会话包含多条用户/助手消息，按时间排序；
- `conversation_id` 为空时创建新会话，会话标题取首条问题前 30 字；
- 会话默认保留 180 天，过期由清理任务删除；
- 当前没有鉴权，`owner_id` 字段预留，暂不校验归属。

### 6.2 历史策略

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `PROMPT_HISTORY_MAX_MESSAGES` | `6` | 直接放入 Prompt 的最近消息数（3 轮） |
| `CONVERSATION_SUMMARY_ENABLED` | `true` | 是否对更早历史做摘要 |
| `CONVERSATION_SUMMARY_MAX_TOKENS` | `400` | 摘要最大 token |
| `CONVERSATION_MAX_TURNS` | `100` | 单会话最大轮数，超出提示新建会话 |

- 最近 3 轮直接进入 Prompt；
- 更早历史压缩为摘要，摘要存放在 `conversation_summaries`；
- 摘要生成失败时退化为只保留最近消息，不阻塞主链路。

### 6.3 权限（当前阶段限制）

- 接口只允许内网或可信服务调用；
- `conversation_id` 不可猜测，但不校验调用方归属；
- 生产前必须补齐认证、会话归属和文档权限过滤；
- 权限落地后，`document_ids` 需要与会话归属取交集。

## 7. 数据模型

### 7.1 `conversations`

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(64) NOT NULL COMMENT '会话 ID',
    title VARCHAR(255) NOT NULL DEFAULT '' COMMENT '会话标题',
    owner_id VARCHAR(64) NULL COMMENT '归属用户，当前预留',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/ARCHIVED',
    message_count INT NOT NULL DEFAULT 0 COMMENT '消息数',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    KEY idx_conversations_owner_updated (owner_id, updated_at),
    KEY idx_conversations_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 7.2 `conversation_messages`

```sql
CREATE TABLE IF NOT EXISTS conversation_messages (
    id VARCHAR(64) NOT NULL COMMENT '消息 ID',
    conversation_id VARCHAR(64) NOT NULL COMMENT '会话 ID',
    role VARCHAR(16) NOT NULL COMMENT 'USER/ASSISTANT',
    content MEDIUMTEXT NOT NULL COMMENT '消息正文',
    status VARCHAR(16) NOT NULL DEFAULT 'COMPLETED' COMMENT 'COMPLETED/REFUSED/PARTIAL/FAILED/CANCELLED/FILTERED',
    rewritten_query VARCHAR(1024) NULL COMMENT '改写后的问题',
    sub_queries_json JSON NULL COMMENT '子查询列表',
    filters_json JSON NULL COMMENT '抽取的过滤条件',
    evidence_json JSON NULL COMMENT '证据列表快照',
    citations_json JSON NULL COMMENT '引用映射',
    usage_json JSON NULL COMMENT 'token 用量',
    timing_json JSON NULL COMMENT '各阶段耗时',
    model VARCHAR(128) NULL COMMENT '生成模型',
    degraded TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否降级',
    error_code VARCHAR(64) NULL COMMENT '错误码',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    PRIMARY KEY (id),
    KEY idx_messages_conversation_created (conversation_id, created_at),
    KEY idx_messages_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 7.3 `conversation_summaries`

```sql
CREATE TABLE IF NOT EXISTS conversation_summaries (
    conversation_id VARCHAR(64) NOT NULL COMMENT '会话 ID',
    summary MEDIUMTEXT NOT NULL COMMENT '历史摘要',
    through_message_id VARCHAR(64) NOT NULL COMMENT '摘要覆盖到的最后一条消息',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 7.4 `message_feedback`

```sql
CREATE TABLE IF NOT EXISTS message_feedback (
    id VARCHAR(64) NOT NULL COMMENT '反馈 ID',
    message_id VARCHAR(64) NOT NULL COMMENT '消息 ID',
    rating VARCHAR(16) NOT NULL COMMENT 'UP/DOWN',
    reason VARCHAR(512) NULL COMMENT '原因标签或补充说明',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    PRIMARY KEY (id),
    KEY idx_feedback_message (message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 7.5 数据保留

- 会话和消息保留 180 天；
- 反馈数据保留 365 天；
- 每天清理过期会话，先删消息和摘要，再删会话；
- 删除会话时同步删除反馈；
- 证据快照和引用快照随消息删除。

## 8. 降级与错误处理

### 8.1 降级矩阵

| 故障 | 行为 | `degraded` |
| --- | --- | --- |
| 查询改写失败/超时 | 使用原始问题检索 | true，`GEN_REWRITE_FAILED` |
| 子查询部分失败 | 使用成功的查询结果 | true，`SUB_QUERY_PARTIAL` |
| 检索单路降级 | 使用检索返回的降级结果 | true，透传原因 |
| 检索完全失败 | 返回 503 | — |
| 主模型不可用 | 切换备模型重试一次 | true，`LLM_FALLBACK` |
| 主备模型都不可用 | 返回 503 | — |
| 首 token 前超时 | 重试 1 次，仍失败返回 503 | — |
| 流式中途超时 | 结束流，消息状态 `PARTIAL`，推送 error 事件 | true |
| 引用校验失败 | 返回答案并标记 `citation_warnings` | true |
| 内容安全过滤 | 消息状态 `FILTERED`，返回固定话术 | — |
| 摘要生成失败 | 只用最近消息 | true，`SUMMARY_FAILED` |

### 8.2 拒答规则

以下情况明确拒答，不调用生成模型：

- 检索结果为空；
- 证据数少于 `GEN_MIN_EVIDENCE_COUNT`（默认 1）；
- 所有证据的 RRF 分数低于 `GEN_MIN_EVIDENCE_SCORE`（默认关闭）。

拒答模板：

```text
知识库中没有找到与问题相关的依据，无法回答。
可以尝试：换个问法、补充时间或范围、或确认相关文档已上传并完成索引。
```

拒答消息 `status=REFUSED`，`citations=[]`，仍然持久化和计入指标。

## 9. 性能目标与容量

### 9.1 性能目标

| 指标 | 目标（P95） | 说明 |
| --- | --- | --- |
| 查询改写 | < 800ms | 短输出，快速路径可跳过 |
| 检索调用 | < 800ms | 复用检索阶段目标 |
| 首 token 延迟 | < 2000ms | 含改写、检索和首 token |
| 完整答案（流式） | < 10s | 300-500 字答案 |
| 引用校验 | < 200ms | basic 级别 |
| 全链路（非流式） | < 12s | 不含前端渲染 |
| 并发 | 20 QPS | 单实例，P95 不劣化超过 50% |

### 9.2 容量估算

| 数据 | 估算 |
| --- | --- |
| 日活会话 | 1000 |
| 日请求 | 2 万 |
| 平均输入 token | 3500 |
| 平均输出 token | 400 |
| 消息保留 180 天 | 约 360 万条 |

成本估算按 DashScope 实际单价在联调阶段补齐。

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GEN_MAX_QUESTION_CHARS` | `2000` | 用户问题最大长度 |
| `GEN_PROMPT_MAX_TOKENS` | `9000` | Prompt 总预算 |
| `GEN_EVIDENCE_MAX_ITEMS` | `10` | 证据条数上限 |
| `GEN_MIN_EVIDENCE_COUNT` | `1` | 少于此值拒答 |
| `GEN_MIN_EVIDENCE_SCORE` | `0` | 证据最低 RRF 分数，0 表示不启用 |
| `GENERATION_MODEL` | `qwen-plus` | 生成模型 |
| `GENERATION_FALLBACK_MODEL` | `qwen-turbo` | 备模型 |
| `GENERATION_TEMPERATURE` | `0.2` | 生成温度 |
| `GENERATION_TOP_P` | `0.8` | 采样参数 |
| `GENERATION_MAX_OUTPUT_TOKENS` | `1024` | 最大输出 token |
| `GENERATION_FIRST_TOKEN_TIMEOUT_SECONDS` | `15` | 首 token 超时 |
| `GENERATION_TOTAL_TIMEOUT_SECONDS` | `120` | 生成总超时 |
| `GENERATION_MAX_RETRIES` | `2` | 首 token 前重试次数 |
| `QUERY_REWRITE_ENABLED` | `true` | 查询改写开关 |
| `QUERY_REWRITE_MODEL` | `qwen-plus` | 改写模型 |
| `QUERY_REWRITE_TIMEOUT_SECONDS` | `8` | 改写超时 |
| `QUERY_REWRITE_MAX_SUB_QUERIES` | `3` | 最大子查询数 |
| `CITATION_CHECK_LEVEL` | `basic` | basic/strict |
| `CITATION_REPAIR_ENABLED` | `true` | 校验失败是否重生成一次 |
| `PROMPT_HISTORY_MAX_MESSAGES` | `6` | 直接进入 Prompt 的历史消息数 |
| `CONVERSATION_SUMMARY_ENABLED` | `true` | 历史摘要开关 |
| `CONVERSATION_RETENTION_DAYS` | `180` | 会话保留天数 |

## 11. 日志与指标

### 11.1 日志

每次请求记录一条结构化日志：

```json
{
  "request_id": "req-...",
  "conversation_id": "conv-...",
  "message_id": "msg-...",
  "question_hash": "sha256:...",
  "rewritten": true,
  "sub_query_count": 1,
  "evidence_count": 5,
  "citation_count": 3,
  "citation_verified": true,
  "status": "COMPLETED",
  "degraded": false,
  "degraded_reasons": [],
  "model": "qwen-plus",
  "usage": {"input_tokens": 3500, "output_tokens": 420},
  "timing_ms": {"rewrite": 320, "retrieval": 260, "generation": 3200, "total": 3808}
}
```

约定：

- 不记录问题原文和答案原文，只记录哈希和长度；
- 摘要、证据正文不进入应用日志；
- 拒答、降级、校验失败、内容过滤必须记录 WARN；
- 日志带 `request_id`，与检索和索引阶段链路贯通。

### 11.2 指标

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `gen_requests_total` | Counter | 请求数，带 `status`、`degraded` 标签 |
| `gen_first_token_latency_ms` | Histogram | 首 token 延迟 |
| `gen_total_latency_ms` | Histogram | 全链路延迟 |
| `gen_tokens_total` | Counter | 输入/输出 token，带 `model` 标签 |
| `gen_rewrite_total` | Counter | 改写成功/失败/跳过 |
| `gen_evidence_count` | Histogram | 证据条数 |
| `gen_refusal_total` | Counter | 拒答次数 |
| `gen_citation_invalid_total` | Counter | 无效引用次数 |
| `gen_content_filtered_total` | Counter | 内容过滤次数 |
| `gen_feedback_total` | Counter | 点赞/点踩数 |

## 12. 测试策略

### 12.1 单元测试

- 请求校验和边界值；
- 会话创建、消息状态流转；
- 改写结果 JSON 解析和校验；
- 多查询合并（RRF）；
- 证据选择、token 预算、引用编号；
- 引用解析和校验规则；
- 降级矩阵逐项覆盖；
- SSE 事件顺序和格式。

### 12.2 集成测试

- 新建会话 → 多轮提问 → 消息和摘要落库；
- 指代消解："那它呢？"能改写出完整问题；
- 有证据时返回带引用答案，引用可点击映射；
- 无证据时走拒答模板；
- 引用校验失败时标记警告或修复；
- 流式输出事件完整，非流式响应字段完整；
- 内容过滤消息状态正确。

### 12.3 故障测试

- 改写超时：回退原始问题；
- 检索 503：返回错误码；
- 主模型 429/5xx：重试后切换备模型；
- 流式中途断开：消息状态 `PARTIAL`，已生成内容保留；
- 客户端主动取消：上游调用被取消，消息状态 `CANCELLED`；
- MySQL 不可用：返回 503；
- 引用校验超时：标记未校验，不阻塞。

### 12.4 性能测试

- 20 并发下首 token P95 < 2000ms；
- 长会话（50 轮）下的历史加载和摘要；
- 10 条证据、9000 token Prompt 的生成延迟；
- 流式和非流式模式对比。

## 13. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-GEN-01 | 请求校验、会话创建、消息落库正确 | 集成测试 |
| AC-GEN-02 | 查询改写支持指代消解、拆解和过滤抽取，失败可回退 | 单元测试、集成测试 |
| AC-GEN-03 | 多查询结果按 RRF 合并，无重复父块 | 单元测试、集成测试 |
| AC-GEN-04 | 证据在 token 预算内组装，引用编号与证据一一对应 | 单元测试 |
| AC-GEN-05 | 答案基于证据生成，引用可解析、可追溯 | 集成测试 |
| AC-GEN-06 | 无证据时明确拒答，不调用生成模型 | 集成测试 |
| AC-GEN-07 | 引用校验发现无效引用并标记或修复 | 集成测试 |
| AC-GEN-08 | 流式 SSE 事件顺序和格式符合协议 | 接口测试 |
| AC-GEN-09 | 降级矩阵逐项生效，`degraded` 标记正确 | 故障注入测试 |
| AC-GEN-10 | 20 并发下首 token P95 < 2000ms，完整答案 P95 < 10s | 性能测试 |
| AC-GEN-11 | 会话、消息、引用、反馈可以完整追溯 | 集成测试 |
| AC-GEN-12 | 单元、集成、故障和性能测试通过 | 测试报告 |

## 14. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-GEN-01 | 查询改写改错意图 | 答非所问 | 改写结果校验、低置信度回退、保留原始问题 |
| R-GEN-02 | 证据不足时模型硬编 | 幻觉 | 强制引用、拒答规则、引用校验 |
| R-GEN-03 | Prompt 注入 | 模型执行证据中的恶意指令 | 证据标签隔离、注入检测、输出校验 |
| R-GEN-04 | 大模型限流或不可用 | 无法生成 | 重试、备模型、明确的错误码 |
| R-GEN-05 | 长上下文 token 成本高 | 费用上升 | token 预算、证据条数上限、摘要压缩 |
| R-GEN-06 | 流式断连导致内容丢失 | 体验差 | 消息状态 `PARTIAL`，支持按消息 ID 拉取完整内容 |
| R-GEN-07 | 引用编号错位 | 用户不信任 | 引用映射后置校验，编号与证据绑定 |
| R-GEN-08 | 当前无鉴权 | 会话和数据越权 | 接口仅内网可用；生产前补齐认证和归属校验 |
| R-GEN-09 | 模型升级导致回答风格变化 | 质量波动 | 固定模型版本，离线评估通过后再切换 |
| R-GEN-10 | 答案涉及敏感信息 | 合规风险 | 内容过滤、日志脱敏、敏感场景切换自建模型 |

## 15. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为生成阶段实施的基线版本。
