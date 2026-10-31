# -*- coding: utf-8 -*-
"""阶段的投递、租约与入口契约；业务检查点仍由阶段处理器管理。"""

from dataclasses import dataclass

from model.document_task.model import DocumentTaskStage as Stage


@dataclass(frozen=True)
class StagePolicy:
    queue: str
    entry: str
    renew_seconds: int
    idle_seconds: int
    soft_time_limit: int
    time_limit: int
    evidence: str
    resume: str


POLICIES = {
    Stage.PENDING: StagePolicy(
        "parse", "parse_document", 120, 900, 300, 330,
        "源文件", "首次领取后进入 PARSING",
    ),
    Stage.PARSING: StagePolicy(
        "parse", "parse_document", 120, 900, 300, 330,
        "源文件 / 已提交分段", "本地重放或接管已有 MinerU 分段",
    ),
    Stage.MERGING: StagePolicy(
        "parse", "parse_document", 120, 600, 300, 330,
        "全部分段的 ZIP", "分段齐全则重新合并，否则回退 PARSING",
    ),
    Stage.CHUNKING: StagePolicy(
        "parse", "parse_document", 120, 600, 900, 930,
        "本版本解析产物", "复用解析产物，否则回退 PARSING",
    ),
    Stage.INDEXING: StagePolicy(
        "index", "index_document", 120, 600, 1740, 1800,
        "本版本子块", "当前保留整版重放；批次续跑在 INDEXING 阶段接入",
    ),
}

PARSE_STAGES = tuple(stage for stage, policy in POLICIES.items() if policy.queue == "parse")
INDEX_STAGES = (Stage.INDEXING,)
MINERU_POLL_SECONDS = 10
REDISPATCH_SECONDS = 60
PENDING_MIN_AGE_SECONDS = 120


def policy_for(stage: Stage) -> StagePolicy:
    """DONE 不是可执行阶段，不能领取或投递。"""
    return POLICIES[Stage(stage)]
