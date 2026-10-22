# 引用、校验与幻觉控制详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-GEN-040 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 生成阶段——引用模型、引用解析、引用校验、修复与拒答、幻觉控制 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义生成阶段的引用体系与答案校验，包括引用模型、引用解析、校验规则、失败修复和拒答策略，并给出幻觉控制的分层措施，作为引用校验模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 引用编号与证据的映射结构；
- 模型输出中引用的解析和规整识别；
- 引用有效性、覆盖率校验；
- 可选的逐句核验（strict 级别）；
- 校验失败的重生成修复和警告标注；
- 无证据拒答和部分证据场景；
- 幻觉控制策略和阈值。

本文档不覆盖：

- 引用编号的生成和 Prompt 约束（见 RAG-GEN-020）；
- 大模型调用与流式输出（见 RAG-GEN-030）；
- 评估指标和反馈闭环（见 RAG-GEN-050）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| 引用 | 答案中的 `[n]` 编号，指向证据列表第 n 条。 |
| 有效引用 | 编号在已分配证据范围内。 |
| 无效引用 | 编号不在证据范围内（模型编造）。 |
| 覆盖率 | 被引用证据数占全部证据数的比例。 |
| 逐句核验 | strict 级别下对每个引用句判断是否被证据支持。 |
| 修复 | 发现无效引用后重新生成一次答案。 |
| 拒答 | 证据不足时不生成答案，返回固定话术。 |

### 1.4 设计依据

- [生成阶段总体设计](./00-overview.md)
- [上下文组装与 Prompt 设计详细设计](./02-context-assembly.md)
- [答案生成与流式输出详细设计](./03-answer-generation.md)
- [RRF 融合详细设计](../retrieval/03-rrf-fusion.md)

### 1.5 设计目标

- 每个 `[n]` 都能解析到唯一证据，编号不错位、不编造；
- 校验结果显式表达：通过、警告、修复、跳过四态；
- 无效引用能被发现并尽可能自动修复；
- 无依据的问题明确拒答，不硬答、不编造；
- 校验成本可控：basic 级别零额外模型调用，strict 可按需开启；
- 校验失败可观测、可追溯、可评估。

## 2. 非目标

- 不做事实核查（Fact Checking）和外部知识比对；
- 不做答案与证据的语义蕴含全量证明；
- 不做多轮答案一致性校验；
- 不做图片、表格内的引用定位；
- 不做引用点击后的正文二次高亮（前端按 `locator` 定位）。

## 3. 引用模型

### 3.1 引用结构

`citations` 数组只包含答案中实际引用的证据条目，按编号升序排列：

```json
{
  "index": 1,
  "parent_id": "p1a2b3c4d5e6...",
  "document_id": "a1b2c3d4e5f6...",
  "document_name": "2026 年财务报告.pdf",
  "file_type": "pdf",
  "locator": "page:10-12",
  "page_start": 10,
  "page_end": 12,
  "heading_path": "第三章 财务分析 > 3.2 收入结构",
  "best_chunk_id": "c9f2a1b3...",
  "score": 0.032
}
```

- `index` 与答案中的 `[n]` 严格一致；
- `best_chunk_id` 为该父块命中排名最好的子块；
- 完整证据（含正文）在 `include_evidence=true` 时通过响应 `evidence` 字段返回，默认不返回。

### 3.2 生命周期

```text
组装阶段：证据编号分配（[1]..[n]），建立「编号 -> 证据」映射
生成阶段：Prompt 约束模型只使用已分配编号
校验阶段：解析答案中的 [n]，核验存在性和覆盖率，必要时修复
响应阶段：把被引用的编号映射为 citations 返回
持久化：   citations_json 落库，含校验统计
```

### 3.3 无引用场景

| 场景 | 处理 |
| --- | --- |
| 拒答 | `citations=[]`，`check_status=REFUSED` |
| 常识寒暄（"你好"） | 不进入生成链路，固定话术回复，`citations=[]` |
| 有效回答但无引用 | 见 5.2 无引用校验 |

## 4. 引用解析

### 4.1 解析格式

支持以下引用形态，统一归一化为 `[n]`：

| 形态 | 归一化 |
| --- | --- |
| `[1]` | `[1]` |
| `[1][2]`、`[1] [2]` | `[1][2]` |
| `[1,2]` | `[1][2]` |
| `[1-3]` | `[1][2][3]` |
| `【1】`、`（1）`、`(1)` | `[1]` |
| `[来源1]` | `[1]` |

### 4.2 解析规则

```text
1. 按行扫描答案，逐字符匹配引用模式
2. 提取编号，去重、排序
3. 排除 Markdown 链接 [文本](url)：仅当方括号内为纯数字或数字列表时视为引用
4. 排除代码块内的方括号内容
5. 输出 parsed_numbers 和 invalid_numbers
```

### 4.3 解析输出

```python
class CitationCheckResult:
    parsed_numbers: list[int]        # 出现在答案中的编号
    invalid_numbers: list[int]       # 超出证据范围的编号
    cited_evidence_count: int        # 被引用的证据数
    evidence_count: int              # 全部证据数
    coverage: float                  # cited_evidence_count / evidence_count
    check_status: str                # PASSED / WARNED / REPAIRED / SKIPPED / REFUSED
    citation_verified: bool
    warnings: list[CitationWarning]
    repair_applied: bool
    latency_ms: int


class CitationWarning:
    code: str        # INVALID_CITATION / NO_CITATION / UNSUPPORTED_CLAIM / CHECK_SKIPPED
    detail: str      # 如 "[3][7]" 或句子编号
```

## 5. 校验规则

### 5.1 basic 级别（默认）

| 规则 | 判定 | 结果 |
| --- | --- | --- |
| 编号存在性 | 每个 `[n]` 都在 `1..evidence_count` 范围内 | 全部有效 → 通过；否则警告 |
| 无引用检测 | 答案长度 ≥ `CITATION_NO_CITATION_MIN_CHARS`（默认 50）且没有任何编号 | 警告 `NO_CITATION` |
| 覆盖率统计 | `cited_evidence_count / evidence_count` | 只记录指标，不影响通过与否 |

`citation_verified` 判定：

```text
citation_verified = (invalid_numbers 为空) 且 (有引用 或 答案长度 < 阈值)
```

### 5.2 无引用校验

- 有证据、有实质回答但零引用：警告 `NO_CITATION`，`citation_verified=false`；
- 短回答（"你好"类、少于 50 字符）不触发；
- 只警告不修复（修复的重生成成本高、收益不确定），记录指标观察比例。

### 5.3 strict 级别（可选，默认关闭）

用于高合规场景（如财务、法务文档问答）：

```text
1. 按句子切分答案（中文标点、换行）
2. 提取含引用的句子及其引用证据
3. 调用校验模型批量判断每句是否被证据支持
4. 不支持的句子标记 UNSUPPORTED_CLAIM
5. 句子数上限 CITATION_STRICT_MAX_SENTENCES（默认 30），超出只检查前 30 句
```

校验 Prompt：

```text
你是引用校验助手。请判断每个句子的结论是否被它引用的证据支持。

判断标准：
- 证据能直接推出或合理概括出句子结论：supported = true；
- 证据未提及、超出范围、数值不一致、结论方向相反：supported = false；
- 只依据给出的证据判断，不使用你自己的知识。

输入：
句子列表：
{sentences}
证据列表：
{evidence}

输出 JSON：
{"results": [{"sentence_id": 1, "supported": true, "reason": ""}]}

只输出 JSON，不输出解释。
```

约定：

- strict 校验每次请求额外消耗一次模型调用，超时 `CITATION_CHECK_TIMEOUT_SECONDS`（默认 5s）；
- 校验失败或超时：`check_status=SKIPPED`，追加警告 `CHECK_SKIPPED`，不影响答案返回；
- strict 结果只警告，默认不触发重生成。

### 5.4 校验超时与失败

| 情况 | 处理 |
| --- | --- |
| 解析异常 | 视为无引用，走 5.2 |
| basic 校验 | 纯本地计算，无超时风险 |
| strict 校验超时 | `SKIPPED` + `CHECK_SKIPPED` 警告 |
| strict 校验返回非法 JSON | `SKIPPED` + 警告，不阻塞 |

## 6. 修复与拒答

### 6.1 无效引用修复

触发条件：

- `invalid_numbers` 非空；
- `CITATION_REPAIR_ENABLED=true`（默认）；
- 主答案生成未使用过修复（每次请求最多 1 次）。

修复流程：

```text
1. 在原对话上下文后追加修复指令
2. 重新调用生成模型（非流式，输出长度上限收紧到 1024）
3. 新答案替换原答案
4. 重新执行 basic 校验
5. 修复后仍无效：保留新答案 + 警告，不再重试
```

修复指令模板：

```text
你上一次的回答引用了不存在的证据编号：[{invalid_numbers}]。
证据编号范围是 [1] 到 [{evidence_count}]。
请重新回答原问题，要求：
1. 只使用范围内的证据编号；
2. 每个事实性结论必须标注引用；
3. 证据不足时如实说明；
4. 不要解释这次修改。
```

约定：

- 修复在流式模式下发生在 `done` 之前：流式内容已推送，无法撤回；修复后的答案通过 `citations` 事件携带的 `repaired_answer` 字段或最终消息拉取生效。为避免前后不一致，**流式模式默认不启用修复**（`CITATION_REPAIR_STREAM=false`），只做警告标注；非流式模式启用修复；
- 修复产生的新答案覆盖消息内容，`repair_applied=true`，`check_status=REPAIRED`；
- 修复调用计入生成模型指标和成本。

### 6.2 警告标注

- `warnings` 随 `citations` 事件和响应返回；
- 前端在答案底部展示提示，例如："部分引用未能验证，请谨慎参考。"
- 不同警告的展示文案：

| 警告 | 前端文案 |
| --- | --- |
| `INVALID_CITATION` | 部分引用编号无效，已尽力修正 |
| `NO_CITATION` | 本条回答未标注引用来源 |
| `UNSUPPORTED_CLAIM` | 部分结论可能缺少充分依据 |
| `CHECK_SKIPPED` | 引用校验未完成 |

### 6.3 拒答场景

拒答发生在生成之前（无证据/证据不足），由编排层处理：

- 消息状态 `REFUSED`；
- `citations=[]`，`check_status=REFUSED`；
- 固定话术见总体设计 8.2 节；
- 拒答率计入指标 `gen_refusal_total`。

生成后的拒答复核（轻量）：

- 答案包含"没有找到相关依据"且证据数 ≥ `GEN_MIN_EVIDENCE_COUNT` 时，记录指标 `gen_unexpected_refusal_total`，用于发现 Prompt 或证据质量问题；
- 不修改答案。

### 6.4 部分证据场景

- 证据只能覆盖问题的一部分时，允许回答并明确说明范围（Prompt 约束："证据不足时如实说明"）；
- 校验角度：`coverage` 低不构成警告，只记录指标；
- strict 级别下未被支持的句子按 5.3 标注。

## 7. 幻觉控制

### 7.1 分层措施

| 阶段 | 措施 |
| --- | --- |
| 生成前 | 证据不足直接拒答；证据编号与 Prompt 严格绑定；低温采样 |
| 生成中 | 系统提示词约束"只依据证据"；格式要求结论句必须标注引用 |
| 生成后 | basic/strict 校验；无效引用修复；警告标注；指标监控 |

### 7.2 阈值与判定

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `GEN_MIN_EVIDENCE_COUNT` | `1` | 少于该条数拒答 |
| `GEN_MIN_EVIDENCE_SCORE` | `0`（关闭） | 证据最低融合分数 |
| `GENERATION_TEMPERATURE` | `0.2` | 低温减少发挥 |
| `CITATION_NO_CITATION_MIN_CHARS` | `50` | 无引用检查阈值 |

### 7.3 质量信号

| 信号 | 来源 | 用途 |
| --- | --- | --- |
| `invalid_citation_ratio` | basic | 无效引用比例，超过 5% 告警 |
| `no_citation_ratio` | basic | 无引用回答比例 |
| `unsupported_ratio` | strict | 不支持句子比例 |
| `unexpected_refusal_ratio` | 复核 | 有证据却拒答的比例 |
| `citation_coverage` | basic | 证据利用情况 |

信号通过指标上报（见 RAG-GEN-050），用于 Prompt 和检索质量迭代。

## 8. 数据记录

消息 `citations_json` 结构：

```json
{
  "citations": [
    {"index": 1, "parent_id": "p1a2...", "document_name": "2026 年财务报告.pdf", "locator": "page:10-12"}
  ],
  "citation_verified": true,
  "check_status": "PASSED",
  "warnings": [],
  "repair_applied": false,
  "stats": {
    "parsed_count": 3,
    "invalid_count": 0,
    "cited_evidence_count": 2,
    "evidence_count": 5,
    "coverage": 0.4
  }
}
```

记录规则：

- `citations` 只存被引用的证据条目；
- `stats` 全量记录，用于评估；
- 修复时保留修复前后统计：`repair_before_invalid`、`repair_after_invalid`；
- strict 校验结果（句子级）只在 strict 开启时记录，正文不入日志。

## 9. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CITATION_CHECK_LEVEL` | `basic` | basic / strict / off |
| `CITATION_REPAIR_ENABLED` | `true` | 修复总开关 |
| `CITATION_REPAIR_STREAM` | `false` | 流式模式是否修复 |
| `CITATION_REPAIR_MAX_ATTEMPTS` | `1` | 修复次数上限 |
| `CITATION_NO_CITATION_MIN_CHARS` | `50` | 无引用检查长度阈值 |
| `CITATION_STRICT_MODEL` | `qwen-plus` | strict 校验模型 |
| `CITATION_STRICT_MAX_SENTENCES` | `30` | strict 检查句子上限 |
| `CITATION_CHECK_TIMEOUT_SECONDS` | `5` | strict 校验超时 |

## 10. 测试

### 10.1 单元测试

- 引用解析各形态归一化：`[1,2]`、`[1-3]`、`【1】`、`[来源1]`；
- Markdown 链接和代码块不误判为引用；
- 无效引用检出、覆盖率计算；
- `citation_verified` 四态判定；
- 无引用阈值边界（49/50/51 字符）；
- 修复指令构造和一次上限；
- `citations_json` 结构完整。

### 10.2 集成测试

- 正常答案引用全部有效：`PASSED`；
- 模型编造编号：检出 `INVALID_CITATION`，非流式触发修复；
- 流式模式检出无效引用：只警告不修复；
- 有证据零引用答案：`NO_CITATION` 警告；
- 拒答：`check_status=REFUSED`，`citations=[]`；
- strict 模式：构造不支持句子，正确标记 `UNSUPPORTED_CLAIM`；
- strict 超时：`SKIPPED` + 警告，答案正常返回。

### 10.3 故障测试

- 修复调用失败：保留原答案 + 警告；
- 校验模型不可用：strict 降级为 `SKIPPED`；
- 解析遇到畸形输入（超长编号、嵌套括号）：不崩溃，按规则忽略。

## 11. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-CV-01 | 引用解析覆盖全部约定形态，不误伤 Markdown 链接 | 单元测试 |
| AC-CV-02 | 无效引用能检出，`citation_verified` 判定正确 | 单元测试 |
| AC-CV-03 | 非流式修复流程生效，最多一次，修复统计完整 | 集成测试 |
| AC-CV-04 | 流式模式只警告不修复，协议不破坏 | 集成测试 |
| AC-CV-05 | 无引用答案触发 `NO_CITATION`，短回答不触发 | 单元测试 |
| AC-CV-06 | 拒答状态和 `citations` 为空正确 | 集成测试 |
| AC-CV-07 | strict 校验可开启、可降级、可观测 | 集成测试 |
| AC-CV-08 | 校验质量信号按指标上报 | 集成测试 |

## 12. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-CV-01 | 模型编造引用编号 | 用户误导 | basic 校验 + 非流式修复 + 告警 |
| R-CV-02 | 引用形态多样导致漏解析 | 漏检 | 形态白名单 + 单测覆盖 + 指标观察 |
| R-CV-03 | 修复引入新错误 | 质量下降 | 修复后复检、统计修复前后指标 |
| R-CV-04 | 流式模式无法修复 | 无效引用直达用户 | 警告标注 + 前端提示 + 评估是否收紧 Prompt |
| R-CV-05 | strict 校验成本高 | 费用上升 | 默认关闭、按文档场景开启、句子数上限 |
| R-CV-06 | 合理引用被误报无效 | 体验差 | 解析规则保守、警告不阻断、人工抽查 |
| R-CV-07 | 拒答话术与实际能力不符 | 用户流失 | 拒答率指标监控、检索和索引侧优化迭代 |

## 13. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为引用校验模块实施的基线版本。
