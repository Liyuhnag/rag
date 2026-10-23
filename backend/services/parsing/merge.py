# -*- coding: utf-8 -*-
"""把 PDF 分段和 Word 的 Markdown 合成一份。"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class MarkdownPage:
    """MinerU 已经按页给出的正文。"""

    page: int
    text: str


@dataclass(frozen=True)
class MarkdownPart:
    """一段待合并的 Markdown。没有逐页正文时使用整段文本。"""

    part_index: int
    page_start: int
    page_end: int
    markdown: str
    pages: tuple[MarkdownPage, ...] = ()


def merge_markdown(parts: Sequence[MarkdownPart]) -> str:
    """按起始页升序拼接，并在每页前插入页码标记。"""
    ordered = sorted(parts, key=lambda part: (part.page_start, part.part_index))
    sections = [_render_part(part) for part in ordered]
    sections = [section for section in sections if section]
    if not sections:
        return ""
    return "\n\n".join(sections) + "\n"


def _render_part(part: MarkdownPart) -> str:
    """一段有逐页正文就按页标记，否则只标记起始页。"""
    if part.pages:
        blocks = [
            _page_block(page.page, page.text, part.part_index)
            for page in part.pages
        ]
        return "\n\n".join(block for block in blocks if block)
    if not part.markdown.strip() and part.page_start <= 0:
        return ""
    page = part.page_start if part.page_start > 0 else 1
    return _page_block(page, part.markdown, part.part_index)


def _page_block(page: int, text: str, part_index: int) -> str:
    """页码标记加正文。图片相对路径改到该段的资源目录。"""
    body = _rewrite_images(text.strip(), part_index)
    marker = f"<!-- page: {page} -->"
    if not body:
        return marker
    if body.startswith(marker):
        return body
    return f"{marker}\n\n{body}"


def _rewrite_images(text: str, part_index: int) -> str:
    """把相对图片链接改成 assets/part-{n}/ 下的路径。"""

    def replace(match: re.Match[str]) -> str:
        target = _asset_target(match.group(2), part_index)
        if target is None:
            return match.group(0)
        return f"![{match.group(1)}]({target})"

    return _IMAGE.sub(replace, text)


def _asset_target(raw: str, part_index: int) -> str | None:
    """外链、绝对路径和会跳出目录的链接保持原样。"""
    target = raw.strip()
    if target.startswith(("http://", "https://", "data:", "/", "#", "assets/")):
        return None
    relative = target.removeprefix("./")
    if ".." in relative.split("/"):
        return None
    return f"assets/part-{part_index}/{relative}"
