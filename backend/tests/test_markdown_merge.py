# -*- coding: utf-8 -*-
"""PDF 分段和 Word 共用同一份 Markdown 合并。"""

from services.parsing.merge import MarkdownPage
from services.parsing.merge import MarkdownPart
from services.parsing.merge import merge_markdown


def test_parts_merge_in_page_order_with_markers():
    merged = merge_markdown(
        [
            MarkdownPart(
                part_index=2,
                page_start=101,
                page_end=200,
                markdown="后半\n\n![图](images/b.png)",
            ),
            MarkdownPart(
                part_index=1,
                page_start=1,
                page_end=100,
                markdown="前半\n\n![图](./images/a.png)",
            ),
        ]
    )

    assert merged == (
        "<!-- page: 1 -->\n\n"
        "前半\n\n"
        "![图](assets/part-1/images/a.png)\n\n"
        "<!-- page: 101 -->\n\n"
        "后半\n\n"
        "![图](assets/part-2/images/b.png)\n"
    )


def test_word_part_uses_the_same_marker():
    merged = merge_markdown(
        [
            MarkdownPart(
                part_index=1,
                page_start=1,
                page_end=0,
                markdown="合同正文",
            )
        ]
    )

    assert merged == "<!-- page: 1 -->\n\n合同正文\n"


def test_per_page_text_replaces_the_part_blob():
    merged = merge_markdown(
        [
            MarkdownPart(
                part_index=1,
                page_start=1,
                page_end=2,
                markdown="不应该出现",
                pages=(
                    MarkdownPage(page=1, text="第一页"),
                    MarkdownPage(page=2, text="第二页 ![图](https://cdn.example/a.png)"),
                ),
            )
        ]
    )

    assert merged == (
        "<!-- page: 1 -->\n\n"
        "第一页\n\n"
        "<!-- page: 2 -->\n\n"
        "第二页 ![图](https://cdn.example/a.png)\n"
    )


def test_existing_marker_is_not_repeated():
    merged = merge_markdown(
        [
            MarkdownPart(
                part_index=1,
                page_start=4,
                page_end=4,
                markdown="<!-- page: 4 -->\n\n已有标记",
            )
        ]
    )

    assert merged == "<!-- page: 4 -->\n\n已有标记\n"


def test_empty_parts_merge_to_empty_text():
    assert merge_markdown([]) == ""
