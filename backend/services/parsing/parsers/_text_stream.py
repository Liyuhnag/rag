# -*- coding: utf-8 -*-
"""固定大小文本读取；编码验证不保留整份文本。"""

from collections.abc import Iterator
from pathlib import Path

from services.parsing.errors import ParseFailed

TEXT_CHARS = 65_536


def file_encoding(path: Path, label: str, *, allow_gbk: bool) -> str:
    """完整验证候选编码，避免已输出内容后才回退编码。"""
    encodings = ("utf-8-sig", "gbk") if allow_gbk else ("utf-8-sig",)
    for encoding in encodings:
        try:
            with path.open(encoding=encoding, newline="") as stream:
                while stream.read(TEXT_CHARS):
                    pass
            return encoding
        except UnicodeDecodeError:
            continue
    expected = "UTF-8 或 GBK" if allow_gbk else "UTF-8"
    raise ParseFailed(f"{label} 不是 {expected} 文本")


def text_chunks(path: Path, encoding: str, *, normalize: bool = False) -> Iterator[str]:
    """换行保留可选；无论物理行长度如何，每次读取最多 64Ki 字符。"""
    with path.open(encoding=encoding, newline=None if normalize else "") as stream:
        while chunk := stream.read(TEXT_CHARS):
            yield chunk
