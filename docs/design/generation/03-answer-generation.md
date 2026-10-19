# 答案生成与流式输出详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-GEN-030 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 生成阶段——模型选择与参数、流式调用、SSE 事件、超时重试、断连取消、输出后处理 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义生成阶段的答案生成，包括大模型选择、调用参数、流式调用流程、SSE 事件协议、超时重试、断连取消、内容过滤和输出后处理，作为答案生成模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 大模型主备选型和调用参数；
- 同步（非流式）和流式（SSE）两种调用模式；
- 首 token 超时、总超时、重试和备模型切换；
- 客户端断连和主动取消处理；
- 内容安全过滤的错误映射；
- 模型输出的清洗和格式规整。

本文档不覆盖：

- Prompt 组装（见 RAG-GEN-020）；
- 查询改写模型调用（见 RAG-GEN-010）；
- 引用解析和校验（见 RAG-GEN-040）；
- 指标和日志定义（见 RAG-GEN-050）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| 首 token | 模型开始输出内容的第一个 token。 |
| 增量（delta） | 流式响应中的单次内容片段。 |
| 备模型 | 主模型失败时切换的备用模型。 |
| 断连 | 客户端提前关闭 SSE 连接。 |
| 中途失败 | 首个 token 已输出后才发生的调用错误。 |
| 后处理 | 对模型输出做清洗、规整和长度控制。 |

### 1.4 设计依据

- [生成阶段总体设计](./00-overview.md)
- [上下文组装与 Prompt 设计详细设计](./02-context-assembly.md)
- 阿里云 DashScope 官方文档（OpenAI 兼容模式）
- [检索阶段总体设计](../retrieval/00-overview.md)

### 1.5 设计目标

- 流式输出体验稳定：首 token 快、增量连续、断流可识别；
- 模型故障可恢复：重试、备模型、明确错误码，不产生重复内容；
- 断连及时止损：客户端关闭后立刻取消上游调用；
- 内容安全合规：命中安全策略时返回固定话术并留痕；
- 输出干净可用：Markdown 可渲染、引用格式统一、长度可控；
- 调用过程可观测：首 token、总耗时、token 用量全部记录。

## 2. 非目标

- 不做多模型路由和模型自动评测；
- 不做生成的并发批处理和离线批量生成；
- 不做推测解码、缓存加速等推理优化；
- 不做多模态生成；
- 不做模型微调；
- 不做前端渲染和交互实现。

## 3. 输入输出

### 3.1 输入

```python
class GenerationInput:
    assembled_context: AssembledContext   # 组装好的 Prompt 各部分
    model: str                            # 主模型
    fallback_model: str                   # 备模型
    stream: bool                          # 是否流式
    parameters: GenerationParameters      # 温度、max_tokens 等
    request_id: str
    conversation_id: str
    message_id: str


class GenerationParameters:
    temperature: float = 0.2
    top_p: float = 0.8
    max_tokens: int = 1024
    seed: int | None = None
```

### 3.2 输出

```python
class GenerationOutput:
    content: str                    # 完整答案
    finish_reason: str              # stop / length / content_filter / partial
    model: str                      # 实际使用的模型
    usage: Usage                    # input/output tokens
    first_token_ms: int
    generation_ms: int
    fallback_used: bool
```

流式模式下 `content` 在过程中通过 `delta` 事件推送，最终结果同时返回给编排层用于持久化和引用校验。

## 4. 模型选择与参数

### 4.1 主备模型

| 角色 | 默认模型 | 说明 |
| --- | --- | --- |
| 主模型 | `qwen-plus` | 质量与成本平衡，默认选择 |
| 备模型 | `qwen-turbo` | 主模型不可用时兜底，质量略降 |
| 改写模型 | `qwen-plus` | 见 RAG-GEN-010，独立配置 |
| 摘要模型 | `qwen-turbo` | 历史摘要，低成本 |

约定：

- 模型通过 `GENERATION_MODEL`、`GENERATION_FALLBACK_MODEL` 配置，不需要发版；
- 模型名称以联调时 DashScope 实际可用版本为准，切换模型必须走离线评估（见 RAG-GEN-050）；
- 模型版本固定并记录到消息 `model` 字段，便于问题回溯；
- 鉴权密钥只从环境变量读取，不落库、不进日志。

### 4.2 调用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `temperature` | `0.2` | 知识问答要求稳定，低温 |
| `top_p` | `0.8` | 常规采样 |
| `max_tokens` | `1024` | 支持一次回答 500-800 字 |
| `seed` | 空 | 仅调试和评估时固定 |
| `stream` | `true` | 由请求决定 |

参数约束：

- `temperature` 合法范围 0-1，配置越界时回退默认值并记日志；
- `max_tokens` 不得超过模型上限，也不得超过 Prompt 预算中的输出预留（默认 1500）；
- 参数按请求生效，不做用户级自定义（当前阶段）。

### 4.3 接口方式

- 使用 DashScope 的 OpenAI 兼容模式（`chat/completions`），客户端使用 `openai` SDK；
- 基础地址和密钥通过环境变量配置：`GENERATION_BASE_URL`、`GENERATION_API_KEY`；
- 流式参数 `stream=true`，增量从 `choices[0].delta.content` 读取；
- 用量从最后一个 chunk 的 `usage` 读取（`stream_options.include_usage=true`）；
- 不直接在业务代码里调用 DashScope 原生 SDK，避免与 OpenAI 兼容层混用。

## 5. 调用流程

### 5.1 非流式

```text
1. 构造请求（messages + 参数）
2. 调用主模型，整体超时 GENERATION_TOTAL_TIMEOUT_SECONDS
3. 成功：取 content 和 usage，返回
4. 失败：按 7 节策略重试或切换备模型
5. 输出后处理（第 8 节）
```

### 5.2 流式

```text
1. 推送 meta 事件（conversation_id、message_id、rewritten_query、evidence_count）
2. 调用主模型（stream=true）
3. 循环读取增量：
   - 收到内容 -> 累计 + 推送 delta
   - 首 token 计时打点
   - 每 15 秒无数据 -> 推送心跳注释
4. 收到 finish_reason：
   - stop -> 正常结束
   - length -> 结束并标记 truncated
   - content_filter -> 按 7.4 处理
5. 推送 citations 事件（引用映射见 RAG-GEN-040）
6. 推送 done 事件（finish_reason、usage、timing）
7. 持久化消息
```

### 5.3 超时控制

| 阶段 | 超时 | 超时行为 |
| --- | --- | --- |
| 建连 | 5s | 重试 |
| 首 token | `GENERATION_FIRST_TOKEN_TIMEOUT_SECONDS`（15s） | 取消本次调用，重试 |
| 增量间隔 | `GENERATION_IDLE_TIMEOUT_SECONDS`（30s） | 视为中途失败 |
| 总时长 | `GENERATION_TOTAL_TIMEOUT_SECONDS`（120s） | 中断并结束流 |

超时控制独立于 HTTP 客户端默认超时，由编排层显式控制。

### 5.4 取消与断连

- 客户端断开 SSE 连接时，编排层必须捕获断开事件并取消上游调用；
- 取消后不再推送任何事件（连接已断）；
- 已生成的内容持久化，消息状态：
  - 有内容 → `CANCELLED`，保留部分内容；
  - 无内容 → `CANCELLED`，内容为空；
- 取消操作记录日志（不记 WARN，属于正常行为）；
- 不支持流式断点续传，客户端按 `message_id` 重新拉取完整消息。

### 5.5 并发控制

- 单实例生成并发上限 `GEN_MAX_CONCURRENCY`（默认 20），用信号量控制；
- 排队等待超过 `GEN_QUEUE_WAIT_TIMEOUT_SECONDS`（默认 5s）返回 503 `GEN_LLM_UNAVAILABLE`；
- 排队和并发使用指标 `gen_llm_inflight`、`gen_llm_queue_wait_ms` 观测；
- 备模型切换同样受并发上限约束，不额外放宽。

## 6. SSE 事件协议

### 6.1 事件类型

| 事件 | 次数 | 载荷 | 说明 |
| --- | --- | --- | --- |
| `meta` | 1 | conversation_id、message_id、rewritten_query、evidence_count | 必须第一个推送 |
| `delta` | 0-N | content | 增量内容 |
| `citations` | 0-1 | citations、citation_verified、citation_warnings | `done` 之前推送 |
| `done` | 0-1 | finish_reason、usage、timing_ms | 正常终止 |
| `error` | 0-1 | code、message | 异常终止 |
| 心跳注释 | 0-N | `: keep-alive` | 空闲 15s 推送 |

### 6.2 顺序与终止

- 顺序：`meta → delta* → citations? → done | error`；
- `done` 和 `error` 互斥，且必须是最后一个事件；
- 收到 `done` 或 `error` 后服务端主动关闭连接；
- 拒答场景：`meta → citations（空）→ delta（拒答模板一次性推送）→ done`；
- 内容过滤场景：`meta → error（GEN_CONTENT_FILTERED）`。

### 6.3 HTTP 约定

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

- 网关和反向代理必须关闭 SSE 缓冲（`X-Accel-Buffering: no`）；
- 事件使用标准 `event:` + `data:` 格式，`data` 为单行 JSON；
- 不发送 `retry:` 字段，由前端自行决定重连策略。

### 6.4 流式示例

```text
event: meta
data: {"conversation_id":"conv-8f3a","message_id":"msg-2b7c","rewritten_query":"2026 年华东区收入结构是什么？","evidence_count":5}

event: delta
data: {"content":"华东区收入占比为 42%"}

event: delta
data: {"content":"，是公司第一大区域[1]。"}

event: citations
data: {"citations":[{"index":1,"parent_id":"p1a2...","document_name":"2026 年财务报告.pdf","locator":"page:10-12"}],"citation_verified":true,"citation_warnings":[]}

event: done
data: {"finish_reason":"stop","usage":{"input_tokens":3500,"output_tokens":120},"timing_ms":{"first_token":1400,"generation":3200}}
```

## 7. 错误处理与降级

### 7.1 错误分类

| 分类 | 示例 | 首 token 前 | 首 token 后 |
| --- | --- | --- | --- |
| 鉴权错误 | 401/403 | 不重试，告警，返回 503 | — |
| 限流 | 429 | 退避重试 | 中途失败处理 |
| 服务端错误 | 5xx | 退避重试 | 中途失败处理 |
| 网络错误 | 连接超时、读超时 | 重试 | 中途失败处理 |
| 参数错误 | 400 | 不重试，返回 500，记录配置问题 | — |
| 内容过滤 | 内容安全策略命中 | 返回 `GEN_CONTENT_FILTERED` | 中途失败处理 |
| 配额耗尽 | 欠费、超量 | 不重试，返回 503，告警 | — |

### 7.2 重试策略

- 仅首 token 前允许重试；
- 最多 `GENERATION_MAX_RETRIES`（默认 2）次，即最多 3 次调用机会（含首次）；
- 退避：0.5s、1s，加 ±20% 抖动；
- 重试与备模型共用总预算，重试不得使总耗时超过 `GENERATION_TOTAL_TIMEOUT_SECONDS`；
- 中途失败不重试，避免输出拼接和重复。

### 7.3 备模型切换

```text
主模型失败
  -> 首 token 前错误
     -> 解除本次调用，切换 GENERATION_FALLBACK_MODEL
     -> 备模型调用一次（不叠加重试）
     -> 成功：degraded=true，原因 LLM_FALLBACK
     -> 失败：error 事件 GEN_LLM_UNAVAILABLE 或 GEN_LLM_TIMEOUT
```

- 备模型只尝试一次；
- 切换原因、耗时、模型名记录到日志和消息 `model` 字段；
- 非流式模式同样适用。

### 7.4 内容过滤

- 调用返回内容过滤相关错误码或 `finish_reason=content_filter` 时：
  - 推送 `error` 事件，code `GEN_CONTENT_FILTERED`；
  - 消息内容写入固定话术：`抱歉，这个问题涉及的内容无法回答。请调整问题后重试。`；
  - 消息状态 `FILTERED`，计数 `gen_content_filtered_total`；
  - 记录触发来源（输入或输出），不记录敏感原文。

### 7.5 中途失败处理

```text
中途失败（增量已输出）
  -> 有内容（累计 >= 1 字符）
     -> 推送 error 事件（GEN_LLM_TIMEOUT / GEN_LLM_UNAVAILABLE）
     -> 已累计内容持久化，消息状态 PARTIAL
  -> 无内容
     -> 推送 error 事件
     -> 消息状态 FAILED
```

- 不推送 `done`；
- 前端可以展示已收到的部分内容和错误提示；
- 消息 `error_code` 记录具体原因。

### 7.6 降级汇总

| 场景 | 响应 | 消息状态 | degraded |
| --- | --- | --- | --- |
| 重试后成功 | 正常 | COMPLETED | true（记录重试） |
| 备模型成功 | 正常 | COMPLETED | true |
| 首 token 前彻底失败 | 503 / error 事件 | FAILED | — |
| 中途失败 | error 事件 | PARTIAL | true |
| 内容过滤 | error 事件 | FILTERED | — |
| 客户端取消 | 无（连接已断） | CANCELLED | — |

## 8. 输出后处理

### 8.1 清洗步骤

按顺序执行：

```text
1. 去除首尾空白和零宽字符
2. 统一换行：连续空行压缩为最多 2 个
3. 引用格式规整（8.2）
4. 移除原始 HTML 危险标签（script/iframe/style），其余 HTML 字符转义
5. 长度控制（8.3）
6. 去重末尾客套话（重复的"希望对您有帮助"等只保留一次）
```

### 8.2 引用格式规整

| 模型输出 | 规整后 |
| --- | --- |
| `[1,2]` | `[1][2]` |
| `[1-3]` | `[1][2][3]` |
| `【1】` | `[1]` |
| `（1）` 结尾 | `[1]` |
| `[1] [2]` | `[1][2]` |

- 规整只处理明确的引用形态，不误伤 Markdown 链接 `[文本](url)`；
- 规整后交由引用校验（RAG-GEN-040）判断编号有效性；
- 规整过程记录 `citation_normalized_count`。

### 8.3 长度控制

- 答案字符上限 `GEN_MAX_ANSWER_CHARS`（默认 8000）；
- 超限时在句子边界截断，追加 `（内容过长已截断）`；
- `finish_reason=length` 时同样标记截断；
- 截断不删除引用编号，保留已生成引用的可追溯性。

### 8.4 Markdown 约定

- 允许：段落、列表、加粗、表格、代码块、行内代码；
- 禁止：一级标题（`#`），由前端渲染标题层级；
- 输出必须是合法 Markdown；渲染安全由前端和后端双重保证（HTML 转义 + 危险标签移除）。

## 9. 性能与容量

### 9.1 性能目标

| 指标 | 目标（P95） | 说明 |
| --- | --- | --- |
| 首 token 延迟 | < 2000ms | 含改写、检索、排队 |
| 增量间隔 | < 500ms | 流式流畅度 |
| 完整生成（300-500 字） | < 8s | 流式出完 |
| 非流式整体 | < 12s | 含全部链路 |
| 备模型切换额外耗时 | < 1s | 决策和重建连 |

### 9.2 容量假设

| 项 | 值 |
| --- | --- |
| 单实例并发 | 20 |
| 单请求输入 token | 平均 3500，上限 9000 |
| 单请求输出 token | 平均 400，上限 1024 |
| 日均请求 | 2 万 |

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GENERATION_MODEL` | `qwen-plus` | 主模型 |
| `GENERATION_FALLBACK_MODEL` | `qwen-turbo` | 备模型 |
| `GENERATION_BASE_URL` | DashScope 兼容地址 | 接口地址 |
| `GENERATION_API_KEY` | 无 | 密钥，从环境变量读取 |
| `GENERATION_TEMPERATURE` | `0.2` | 温度 |
| `GENERATION_TOP_P` | `0.8` | 采样 |
| `GENERATION_MAX_OUTPUT_TOKENS` | `1024` | 最大输出 |
| `GENERATION_FIRST_TOKEN_TIMEOUT_SECONDS` | `15` | 首 token 超时 |
| `GENERATION_IDLE_TIMEOUT_SECONDS` | `30` | 增量间隔超时 |
| `GENERATION_TOTAL_TIMEOUT_SECONDS` | `120` | 总超时 |
| `GENERATION_MAX_RETRIES` | `2` | 重试次数 |
| `GENERATION_MAX_CONCURRENCY` | `20` | 并发上限 |
| `GEN_QUEUE_WAIT_TIMEOUT_SECONDS` | `5` | 排队等待上限 |
| `GEN_MAX_ANSWER_CHARS` | `8000` | 答案字符上限 |
| `SSE_HEARTBEAT_SECONDS` | `15` | 心跳间隔 |

## 11. 测试

### 11.1 单元测试

- 参数校验和越界回退；
- 超时判定：建连、首 token、增量间隔、总时长；
- 重试退避和次数上限；
- 备模型切换条件；
- SSE 事件序列和终态断言；
- 引用格式规整各输入形态；
- 输出清洗和长度截断；
- HTML 危险标签移除。

### 11.2 集成测试

- 流式问答端到端：meta/delta/citations/done 全事件正确；
- 非流式问答字段完整；
- 拒答路径：meta → citations（空）→ delta → done；
- 首 token 前失败切换到备模型并标记 degraded；
- 中途失败输出 error 事件，消息状态 `PARTIAL`；
- 内容过滤返回 `GEN_CONTENT_FILTERED`，状态 `FILTERED`；
- 客户端断开后上游调用被取消，消息状态 `CANCELLED`。

### 11.3 故障测试

- 模拟 429/5xx/连接超时：按策略重试、切换、报错；
- 模拟增量长时间不返回：空闲超时触发；
- 模拟网关缓冲：确认 `X-Accel-Buffering` 生效，事件实时到达；
- 并发打满：排队、超时和 503 行为正确。

## 12. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-AG-01 | 流式事件顺序、格式、终态符合协议 | 接口测试 |
| AC-AG-02 | 首 token P95 < 2000ms，完整生成 P95 < 8s | 性能测试 |
| AC-AG-03 | 主模型失败能重试、切换备模型并标记降级 | 故障注入测试 |
| AC-AG-04 | 中途失败不重试，不产生重复内容 | 故障注入测试 |
| AC-AG-05 | 客户端断开能取消上游调用并正确落库 | 集成测试 |
| AC-AG-06 | 内容过滤可识别、话术固定、状态正确 | 集成测试 |
| AC-AG-07 | 输出清洗、引用规整、长度控制符合规则 | 单元测试 |
| AC-AG-08 | token 用量、首 token 和总耗时全部记录 | 集成测试 |

## 13. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-AG-01 | 网关缓冲导致流式不明显 | 体验差 | `X-Accel-Buffering: no`、心跳、联调验证 |
| R-AG-02 | 首 token 超时造成整体失败 | 可用性下降 | 重试 + 备模型 + 快速失败 |
| R-AG-03 | 中途失败产生半截答案 | 用户困惑 | error 事件 + 状态 `PARTIAL` + 前端提示 |
| R-AG-04 | 重试导致重复内容 | 答案错乱 | 仅首 token 前重试，中途不重试 |
| R-AG-05 | 模型限流影响并发 | 大量 503 | 并发控制、排队、告警、错峰 |
| R-AG-06 | 断连未取消上游调用 | 浪费配额 | 显式取消、指标观测 |
| R-AG-07 | 模型输出违规内容 | 合规风险 | 内容过滤、后处理、人工排查队列 |
| R-AG-08 | 模型版本升级行为变化 | 质量波动 | 版本固定、离线评估门禁 |

## 14. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为答案生成模块实施的基线版本。
