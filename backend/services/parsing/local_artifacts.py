# -*- coding: utf-8 -*-
"""单遍消费本地解析块，将大产物写磁盘后上传。"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument
from services.parsing.ports import ObjectReader

if TYPE_CHECKING:
    from services.parsing.collect import ArtifactKeys


def store_document(
    parsed: ParsedDocument, reader: ObjectReader, now: datetime
) -> "ArtifactKeys":
    """先完成解析和序列化，再上传产物，最后写完成清单。"""
    from services.parsing.collect import ArtifactKeys
    from services.parsing.collect import _timestamp

    request = parsed.request
    base = f"documents/{request.document_id}/parsed/{request.version}"
    keys = ArtifactKeys(
        f"{base}/merged.md", f"{base}/merged.json", f"{base}/manifest.json"
    )
    with tempfile.TemporaryDirectory(prefix="parsed-local-") as directory:
        md_path = Path(directory) / "merged.md"
        json_path = Path(directory) / "merged.json"
        _serialize(parsed, md_path, json_path)
        reader.upload_file(keys.md_key, md_path, "text/markdown; charset=utf-8")
        reader.upload_file(keys.json_key, json_path, "application/json")
        manifest = {
            "document_id": request.document_id,
            "file_type": request.file_type,
            "page_count": 0,
            "parse_version": request.version,
            "source_key": request.object_key,
            "merged_md_key": keys.md_key,
            "merged_json_key": keys.json_key,
            "parts": [],
            "created_at": _timestamp(now),
        }
        reader.write_object(
            keys.manifest_key,
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )
    return keys


def _serialize(parsed: ParsedDocument, md_path: Path, json_path: Path) -> None:
    """保持 Markdown 原文或段落续片，JSON 按块增量写入。"""
    blocks = iter(parsed.blocks)
    try:
        with md_path.open("w", encoding="utf-8", newline="") as markdown:
            with json_path.open("w", encoding="utf-8", newline="") as output:
                header = {"type": parsed.request.file_type, "title": parsed.title}
                prefix = json.dumps(header, ensure_ascii=False)
                output.write(prefix[:-1] + ', "blocks": [')
                first = True
                has_text = False
                verbatim = False
                for block in blocks:
                    if not first:
                        output.write(", ")
                    json.dump(_block(block), output, ensure_ascii=False)
                    first = False
                    if block.metadata.get("verbatim"):
                        verbatim = True
                        markdown.write(block.text)
                        continue
                    text = block.text
                    if not text:
                        continue
                    if has_text and not block.metadata.get("continuation"):
                        markdown.write("\n\n")
                    markdown.write(text)
                    has_text = True
                if has_text and not verbatim:
                    markdown.write("\n")
                output.write("]}")
    finally:
        close = getattr(blocks, "close", None)
        if close is not None:
            close()


def _block(block: ParsedBlock) -> dict[str, object]:
    """持久化切块需要的定位字段与续片标记。"""
    item: dict[str, object] = {"kind": block.kind, "text": block.text}
    if block.page is not None:
        item["page"] = block.page
    if block.sheet:
        item["sheet"] = block.sheet
    if block.heading_path:
        item["heading_path"] = block.heading_path
    if block.locator:
        item["locator"] = block.locator
    if block.metadata:
        item["metadata"] = block.metadata
    return item
