# -*- coding: utf-8 -*-
"""配置直接对应框架的两级块大小和重叠。"""

import pytest

from services.chunking.common.models import ChunkOptions


def test_default_options_match_two_native_levels():
    options = ChunkOptions()
    assert (options.parent_chunk_size, options.child_chunk_size) == (1600, 400)
    assert options.chunk_overlap == 80


@pytest.mark.parametrize(
    "values",
    [
        {"child_chunk_size": 0},
        {"parent_chunk_size": 200, "child_chunk_size": 400},
        {"chunk_overlap": -1},
        {"chunk_overlap": 400},
    ],
)
def test_invalid_native_chunk_options_fail(values):
    with pytest.raises(ValueError):
        ChunkOptions(**values)
