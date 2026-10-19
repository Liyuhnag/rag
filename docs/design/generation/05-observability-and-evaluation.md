# 可观测与效果评估详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-GEN-050 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 生成阶段——日志与脱敏、指标、会话审计、离线评估、线上反馈、告警 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义生成阶段的日志、指标、会话审计、离线评估、线上反馈和告警，明确质量指标的判定口径和迭代门禁，作为可观测与评估模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 生成链路各阶段的结构化日志和脱敏规则；
- 延迟、质量、成本、安全四类指标的定义和采集；
- 会话、消息、引用、反馈的审计追溯；
- 离线评估集、评估指标、评估流程和发布门禁；
- 点赞点踩反馈和 badcase 流转；
- 告警规则和值班响应。

本文档不覆盖：

- 基础设施监控（CPU、内存、磁盘、网络）；
- 检索阶段内部指标（见 RAG-RET 文档集）；
- 索引阶段任务指标（见 RAG-IDX 文档集）；
- 具体监控平台选型（Prometheus + Grafana 为默认假设）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| 忠实度（Faithfulness） | 答案中的事实性陈述被证据支持的比例。 |
| 答案相关性（Answer Relevance） | 答案是否直接回应用户问题。 |
| 引用准确率（Citation Accuracy） | 引用编号指向正确的比例。 |
| 拒答正确率（Refusal Correctness） | 应拒答时拒答、不应拒答时不拒答。 |
| 门禁（Gate） | 发布前必须通过的评估阈值。 |
| badcase | 质量不合格且需要人工跟进的问答样本。 |
| LLM Judge | 用大模型作为评估裁判。 |

### 1.4 设计依据

- [生成阶段总体设计](./00-overview.md)
- [查询理解与改写详细设计](./01-query-understanding.md)
- [引用、校验与幻觉控制详细设计](./04-citation-verification.md)
- [检索阶段总体设计](../retrieval/00-overview.md)

### 1.5 设计目标

- 每次请求的关键行为可观测，问题可定位到阶段；
- 质量不靠感觉：用离线评估集量化，用门禁管住发布；
- 日志不含敏感原文，满足合规要求；
- 用户反馈能自动进入 badcase 流程并驱动迭代；
- 异常能在分钟级被发现和告警。

## 2. 非目标

- 不做全链路分布式追踪平台建设（本期用 `request_id` + 结构化日志替代）；
- 不做 A/B 实验平台建设；
- 不做用户行为分析（埋点、漏斗）；
- 不做模型自动重训练；
- 不做评估集的全自动标注。

## 3. 日志

### 3.1 日志分层

| 日志 | 内容 | 存储 |
| --- | --- | --- |
| 应用日志 | 请求处理、阶段耗时、错误、降级 | 文件 + 日志平台，保留 30 天 |
| 审计数据 | 会话、消息、引用、反馈 | MySQL，按 180 天保留 |
| 评估数据 | 评估运行结果、指标值 | 文件 + 数据库表，长期保留 |

### 3.2 结构化日志字段

每次请求结束输出一条 JSON 日志：

```json
{
  "log_type": "gen_request",
  "request_id": "req-8f3a2b",
  "conversation_id": "conv-8f3a",
  "message_id": "msg-2b7c",
  "question_hash": "sha256:1a2b3c...",
  "question_chars": 12,
  "rewrite": {
    "method": "llm",
    "is_follow_up": true,
    "sub_query_count": 1,
    "confidence": 0.92,
    "latency_ms": 320
  },
  "retrieval": {
    "evidence_count": 5,
    "matched_child_total": 8,
    "degraded": false,
    "degraded_reasons": [],
    "latency_ms": 260
  },
  "assemble": {
    "token_estimate": 6200,
    "dropped_evidence": 0,
    "truncated_evidence": 1,
    "injection_flags": 0
  },
  "generation": {
    "model": "qwen-plus",
    "fallback_used": false,
    "first_token_ms": 1400,
    "generation_ms": 3200,
    "input_tokens": 3500,
    "output_tokens": 180,
    "finish_reason": "stop"
  },
  "citation": {
    "check_status": "PASSED",
    "parsed_count": 3,
    "invalid_count": 0,
    "coverage": 0.4,
    "repair_applied": false
  },
  "status": "COMPLETED",
  "degraded": false,
  "degraded_reasons": [],
  "total_ms": 3808
}
```

### 3.3 脱敏规则

- 不记录问题和答案原文，只记录哈希和长度；
- 证据正文、摘要正文、Prompt 全文不进入应用日志；
- 模型密钥、`GENERATION_API_KEY` 永不落日志；
- 异常栈允许记录，但需过滤请求体；
- 会话正文只存在 MySQL，通过权限控制访问；
- 导出审计数据时对疑似 PII（手机号、身份证号、银行卡号）做掩码。

### 3.4 日志级别

| 级别 | 场景 |
| --- | --- |
| INFO | 正常请求、降级但成功 |
| WARN | 改写失败、引用校验失败、拒答、摘要失败 |
| ERROR | 检索不可用、模型不可用、持久化失败、内容过滤 |
| DEBUG | 仅本地调试，线上关闭 |

采样：

- INFO 日志按 `GEN_LOG_SAMPLE_RATE`（默认 1.0）采样；
- WARN 和 ERROR 全量记录，不采样。

## 4. 指标

### 4.1 延迟指标

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `gen_rewrite_latency_ms` | Histogram | 改写耗时，带 `method` 标签 |
| `gen_retrieval_latency_ms` | Histogram | 检索调用耗时 |
| `gen_first_token_latency_ms` | Histogram | 首 token 延迟 |
| `gen_generation_latency_ms` | Histogram | 生成耗时 |
| `gen_total_latency_ms` | Histogram | 全链路耗时 |
| `gen_queue_wait_ms` | Histogram | 并发排队等待 |
| `gen_llm_inflight` | Gauge | 进行中的模型调用数 |

### 4.2 质量指标

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `gen_requests_total` | Counter | 请求数，带 `status`、`degraded` 标签 |
| `gen_refusal_total` | Counter | 拒答次数 |
| `gen_unexpected_refusal_total` | Counter | 有证据却拒答 |
| `gen_citation_invalid_total` | Counter | 无效引用次数 |
| `gen_citation_no_citation_total` | Counter | 无引用回答次数 |
| `gen_citation_unsupported_total` | Counter | strict 不支持句子次数 |
| `gen_citation_verified_ratio` | Gauge | 引用校验通过率（滚动窗口） |
| `gen_evidence_count` | Histogram | 证据条数分布 |
| `gen_feedback_total` | Counter | 反馈数，带 `rating` 标签 |

### 4.3 成本指标

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `gen_input_tokens_total` | Counter | 输入 token，带 `model`、`stage` 标签 |
| `gen_output_tokens_total` | Counter | 输出 token |
| `gen_llm_calls_total` | Counter | 模型调用次数，带 `model`、`purpose` 标签 |
| `gen_estimated_cost` | Counter | 估算费用（按配置单价） |

`stage` 取值：`rewrite`、`summary`、`generation`、`citation_strict`。

### 4.4 安全与异常指标

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `gen_content_filtered_total` | Counter | 内容过滤次数 |
| `gen_injection_detected_total` | Counter | 注入检测命中次数 |
| `gen_error_total` | Counter | 错误数，带 `code` 标签 |
| `gen_llm_fallback_total` | Counter | 备模型切换次数 |
| `gen_partial_total` | Counter | 中途失败（PARTIAL）次数 |
| `gen_cancelled_total` | Counter | 客户端取消次数 |

### 4.5 看板

默认建立四个看板：

1. **链路健康**：QPS、延迟分位、错误率、排队、并发；
2. **质量**：拒答率、引用通过率、无效引用率、反馈分布；
3. **成本**：token 用量、调用次数、估算费用趋势；
4. **安全**：内容过滤、注入检测、异常错误码。

## 5. 会话审计与反馈

### 5.1 审计范围

- 会话和消息全量落库（见总体设计 7 节）；
- 每条助手消息可追溯：改写结果、证据快照、引用、用量、耗时、模型、状态；
- 审计数据只读查询，按会话 ID 或消息 ID 检索；
- 审计导出需要审批，导出内容含 PII 时掩码。

### 5.2 反馈采集

反馈接口：

```text
POST /api/chat/messages/{message_id}/feedback
{
  "rating": "UP | DOWN",
  "reason": "答非所问 | 引用错误 | 内容不全 | 信息过时 | 格式问题 | 其他"
}
```

规则：

- 同一消息允许修改反馈，以最后一次为准；
- 点踩时 `reason` 建议必填，允许补充文字说明（≤ 512 字符）；
- 反馈写入 `message_feedback`，同步更新指标；
- 匿名使用阶段 `owner_id` 为空，反馈照常记录。

### 5.3 badcase 流转

```text
点踩 / 引用校验失败 / 非预期拒答
  -> 自动进入 badcase 池（按周汇总）
  -> 分类归因：检索问题 / 改写问题 / 证据问题 / 生成问题 / 预期行为
  -> 结论：
     检索或索引问题 -> 反馈给对应阶段优化
     生成或 Prompt 问题 -> 更新 Prompt 或参数
     合理回答 -> 补充到评估集为"期望拒绝点踩"
  -> 跟踪到关闭，记录处理人和结论
```

约定：

- badcase 池每周评审一次，重大线上事故即时评审；
- 每条 badcase 必须给出归因和处理结果；
- 已关闭的 badcase 中，典型样本进入离线评估集。

## 6. 链路追踪

- `request_id` 由入口生成，贯穿生成、检索、索引查询和日志；
- 生成阶段写入消息 `timing_json`，检索阶段返回的耗时透传记录；
- 跨服务调用在请求头携带 `X-Request-Id`；
- 日志检索以 `request_id` 和 `message_id` 为主键；
- 后续接入 OpenTelemetry 时，`request_id` 作为 trace 属性保留。

## 7. 离线评估

### 7.1 评估集

| 项 | 要求 |
| --- | --- |
| 规模 | 首批 ≥ 100 题，季度扩充 |
| 覆盖面 | 单文档、跨文档、多轮追问、表格数据、无答案（应拒答）、注入样本 |
| 标注内容 | 标准答案要点、期望引用文档、是否应拒答 |
| 维护 | 由产品 + 研发共同评审，版本化管理（`golden_set_v1.json`） |
| 存放 | 仓库 `tests/eval/fixtures/`，敏感内容脱敏 |

### 7.2 评估指标

| 指标 | 定义 | 目标（V1.0 门禁） |
| --- | --- | --- |
| 忠实度 | 事实性陈述被证据支持的比例 | ≥ 0.90 |
| 引用准确率 | 引用编号指向正确证据的比例 | ≥ 0.95 |
| 答案相关性 | 答案直接回应问题的比例 | ≥ 0.85 |
| 拒答正确率 | 应拒答正确拒答、不应拒答正常回答的比例 | ≥ 0.90 |
| 首 token 延迟 | 评估环境 P95 | < 2000ms |
| 全链路延迟 | 评估环境 P95 | < 12s |

评分方式：

- 自动化：LLM Judge（默认 `qwen-max`）+ 固定 Prompt + 固定 seed；
- 人工：每周抽检自动评分的 10%，校准 Judge 一致性；
- Judge 与人工偏差超过 15% 时，修订 Judge Prompt 并重新校验。

### 7.3 评估流程

```text
1. 准备评估集和候选配置（Prompt / 模型 / 参数）
2. 离线批量跑评估（非流式、固定 seed、关闭 rerank 变量）
3. 自动打分 -> 生成评估报告
4. 人工抽检 -> 修正评分
5. 与基线对比，检查门禁
6. 通过：发布；不通过：归因迭代后重跑
```

### 7.4 发布门禁

以下变更必须跑离线评估并全部达标才能发布：

- system 提示词或组装模板变更；
- 生成模型、改写模型、备模型切换；
- `temperature`、`max_tokens`、证据条数等参数变更；
- 引用校验策略变更（basic/strict、修复开关）；
- 历史窗口和摘要策略变更。

评估报告归档，记录配置差异、指标对比和结论。

## 8. 线上反馈闭环

```text
用户点踩 -> 反馈入库 -> 指标告警（点踩率突增）
  -> badcase 池 -> 周评审 -> 归因 -> 修复项排期
  -> 修复上线 -> 观察指标 -> 抽样验证 -> 关闭
```

- 点踩率（`rating=DOWN` 占比）周度趋势作为质量北极星指标之一；
- 引用错误类点踩优先处理，直接关联引用校验和 Prompt；
- 修复上线后 7 天内对比同题反馈，验证是否收敛。

## 9. 告警

| 告警 | 条件 | 级别 |
| --- | --- | --- |
| 首 token 延迟劣化 | P95 > 3s 持续 10 分钟 | P2 |
| 全链路延迟劣化 | P95 > 15s 持续 10 分钟 | P2 |
| 错误率升高 | `gen_error_total` 比例 > 5% 持续 5 分钟 | P1 |
| 检索不可用 | `GEN_RETRIEVAL_UNAVAILABLE` 出现 | P1 |
| 模型不可用 | `GEN_LLM_UNAVAILABLE` 比例 > 1% | P1 |
| 无效引用升高 | `invalid_citation_ratio` > 5% 持续 30 分钟 | P2 |
| 拒答率异常 | 拒答率 > 30% 且环比翻倍 | P2 |
| 内容过滤异常 | 过滤次数突增 10 倍 | P2 |
| 排队积压 | `gen_queue_wait_ms` P95 > 5s | P2 |
| 成本异常 | 日费用超过预算 120% | P3 |

处理约定：

- P1 立即响应，15 分钟内跟进；
- P2 当班处理；
- P3 次日处理；
- 所有告警处理记录在值班日志，重大告警复盘。

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GEN_LOG_SAMPLE_RATE` | `1.0` | INFO 日志采样率 |
| `GEN_METRICS_ENABLED` | `true` | 指标开关 |
| `GEN_COST_INPUT_PER_1K` | `0` | 输入 token 单价（元/千 token） |
| `GEN_COST_OUTPUT_PER_1K` | `0` | 输出 token 单价，0 表示不估算 |
| `EVAL_GOLDEN_SET_PATH` | `tests/eval/fixtures/golden_set_v1.json` | 评估集路径 |
| `EVAL_JUDGE_MODEL` | `qwen-max` | Judge 模型 |
| `EVAL_FAITHFULNESS_THRESHOLD` | `0.90` | 忠实度门禁 |
| `EVAL_CITATION_ACCURACY_THRESHOLD` | `0.95` | 引用准确率门禁 |
| `EVAL_RELEVANCE_THRESHOLD` | `0.85` | 答案相关性门禁 |
| `EVAL_REFUSAL_CORRECTNESS_THRESHOLD` | `0.90` | 拒答正确率门禁 |

## 11. 测试

### 11.1 单元测试

- 日志字段完整、脱敏字段不出现在输出；
- 指标标签取值正确；
- 反馈接口校验（rating 枚举、reason 长度、修改反馈）；
- 评估集加载和评分计算；
- 费用估算公式。

### 11.2 集成测试

- 一次请求后日志、指标、审计数据一致；
- 点踩反馈入库并更新指标；
- badcase 池能正确拉取点踩、校验失败和非预期拒答样本；
- 评估脚本可复现（同配置两次跑分差异 < 1%）；
- 告警规则触发和恢复。

### 11.3 演练

- 模拟模型大面积 5xx：观察告警和错误率指标；
- 模拟点踩率突增：验证告警和 badcase 流程；
- 模拟成本超限：验证费用告警。

## 12. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-OB-01 | 请求日志字段完整，问题/答案原文不落日志 | 单元测试、代码审查 |
| AC-OB-02 | 延迟、质量、成本、安全指标按定义上报 | 集成测试 |
| AC-OB-03 | 会话、消息、引用、反馈可审计追溯 | 集成测试 |
| AC-OB-04 | 评估集覆盖六类场景，评分可复现 | 评估报告 |
| AC-OB-05 | 发布门禁流程生效，变更需评估报告 | 流程检查 |
| AC-OB-06 | 点踩反馈进入 badcase 池并完成闭环 | 集成测试、周评审记录 |
| AC-OB-07 | 告警规则按级别触发，值班可响应 | 演练记录 |

## 13. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-OB-01 | LLM Judge 与人工评分偏差大 | 门禁失真 | 每周人工抽检 10%，偏差超限修订 Judge |
| R-OB-02 | 评估集覆盖不足 | 漏检回归 | 季度扩充、badcase 转评估样本 |
| R-OB-03 | 日志泄露敏感内容 | 合规风险 | 哈希化、字段级脱敏、代码审查 |
| R-OB-04 | 指标告警噪音大 | 疲劳 | 阈值调优、分级、抑制窗口 |
| R-OB-05 | 反馈量不足 | 迭代缺输入 | 前端引导、周度样本复盘、主动抽检 |
| R-OB-06 | 评估成本高 | 迭代变慢 | 评估集规模控制、抽样评估、Judge 缓存 |

## 14. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为可观测与评估模块实施的基线版本。
