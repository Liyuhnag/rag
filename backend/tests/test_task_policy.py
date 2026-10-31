# -*- coding: utf-8 -*-
"""公共阶段契约不能把任务路由到错误队列或过早回收。"""

import pytest

from model.document_task.model import DocumentTaskStage as Stage
from services.task_runtime.lease import TaskLease
from services.task_runtime.policy import POLICIES
from services.task_runtime.policy import policy_for


def test_all_executable_stages_have_safe_lease_and_execution_limits():
    assert set(POLICIES) == set(Stage) - {Stage.DONE}
    for policy in POLICIES.values():
        assert policy.idle_seconds >= 4 * policy.renew_seconds
        assert 0 < policy.soft_time_limit < policy.time_limit
        assert policy.evidence and policy.resume
    assert policy_for(Stage.PARSING).idle_seconds > 300
    with pytest.raises(KeyError):
        policy_for(Stage.DONE)


def test_queue_ownership_and_lease_defaults_come_from_policy():
    for stage in POLICIES:
        expected_queue = "index" if stage == Stage.INDEXING else "parse"
        assert policy_for(stage).queue == expected_queue
        lease = TaskLease("task", "token", stages=(stage,))
        assert lease.interval == policy_for(stage).renew_seconds
