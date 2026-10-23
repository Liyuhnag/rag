# -*- coding: utf-8 -*-
"""收集 MinerU 分段结果，合并后生成要写入对象存储的文件。"""

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

from services.parsing.errors import ParseFailed
from services.parsing.merge import MarkdownPart
from services.parsing.merge import merge_markdown
from services.parsing.models import ParsedBlock
from services.parsing.models import ParsedDocument

_MAX_FILES = 10_000
_MAX_BYTES = 1024 * 1024 * 1024
_MARKDOWN = {".md", ".markdown"}
_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_ALLOWED = _MARKDOWN | _IMAGES | {".json", ".txt"}
_CONTENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass(frozen=True)
class PartZip:
    """一个 MinerU 分段的结果压缩包。"""

    part_index: int
    page_start: int
    page_end: int
    content: bytes


@dataclass(frozen=True)
class StoredObject:
    """准备写入对象存储的一个文件。"""

    key: str
    body: bytes
    content_type: str


@dataclass(frozen=True)
class ArtifactKeys:
    """合并产物在对象存储中的位置。"""

    md_key: str
    json_key: str
    manifest_key: str
    part_results: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class CollectedParse:
    """合并后的产物和对应的对象列表。"""

    result: ArtifactKeys
    objects: tuple[StoredObject, ...]


def collect_parts(
    document_id: str,
    version: int,
    file_type: str,
    source_key: str,
    parts: tuple[PartZip, ...] | list[PartZip],
    now: datetime,
) -> CollectedParse:
    """解压各段，合并 Markdown，并带上图片和清单。"""
    if not parts:
        raise ParseFailed("没有可合并的解析结果")
    extracted = [_extract_part(part) for part in parts]
    merged = merge_markdown(
        [
            MarkdownPart(
                part_index=item.part_index,
                page_start=item.page_start,
                page_end=item.page_end,
                markdown=item.markdown,
            )
            for item in extracted
        ]
    )
    return _package(
        document_id=document_id,
        version=version,
        file_type=file_type,
        source_key=source_key,
        merged=merged,
        page_count=_page_count(extracted),
        now=now,
        part_files=tuple(_part_objects(document_id, version, item) for item in extracted),
    )


def collect_document(parsed: ParsedDocument, now: datetime) -> CollectedParse:
    """把本地解析结果收成同一套 merged.md 和 merged.json。"""
    request = parsed.request
    texts = [block.text.strip() for block in parsed.blocks if block.text.strip()]
    merged = "\n\n".join(texts) + "\n" if texts else ""
    payload = {
        "type": request.file_type,
        "title": parsed.title,
        "blocks": [_block(block) for block in parsed.blocks],
    }
    return _package(
        document_id=request.document_id,
        version=request.version,
        file_type=request.file_type,
        source_key=request.object_key,
        merged=merged,
        page_count=0,
        now=now,
        part_files=(),
        extra_json=payload,
    )


@dataclass(frozen=True)
class _ExtractedPart:
    part_index: int
    page_start: int
    page_end: int
    markdown: str
    markdown_bytes: bytes
    images: tuple[tuple[str, bytes], ...]
    content_json: bytes | None


def _extract_part(part: PartZip) -> _ExtractedPart:
    """从压缩包取出 Markdown 和图片。"""
    files = _unzip(part.content)
    markdown_name, markdown_bytes = _choose_markdown(files)
    text = _decode_markdown(markdown_bytes)
    images = _images_beside(markdown_name, files)
    content_json = _content_json(markdown_name, files)
    return _ExtractedPart(
        part_index=part.part_index,
        page_start=part.page_start,
        page_end=part.page_end,
        markdown=text,
        markdown_bytes=markdown_bytes,
        images=images,
        content_json=content_json,
    )


def _unzip(content: bytes) -> dict[str, bytes]:
    """安全解压。拒绝越界路径、过多文件和过大体积。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ParseFailed("MinerU 结果不是有效的压缩包") from exc
    files: dict[str, bytes] = {}
    total = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)
            suffix = Path(name).suffix.lower()
            if suffix not in _ALLOWED:
                continue
            if name in files:
                raise ParseFailed("MinerU 结果包含重复文件")
            if len(files) >= _MAX_FILES:
                raise ParseFailed("MinerU 结果文件过多")
            data = _read_member(archive, info, _MAX_BYTES - total)
            total += len(data)
            files[name] = data
    return files


def _safe_name(filename: str) -> str:
    """拒绝绝对路径、空名和跳出目录的路径。"""
    if (
        not filename
        or "\\" in filename
        or "\x00" in filename
        or filename.startswith("/")
    ):
        raise ParseFailed("MinerU 结果路径不安全")
    parts = Path(filename).parts
    if not parts or ".." in parts:
        raise ParseFailed("MinerU 结果路径不安全")
    return Path(filename).as_posix()


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    remaining: int,
) -> bytes:
    """读取一个文件，超过剩余额度就失败。"""
    if remaining < 0:
        raise ParseFailed("MinerU 结果过大")
    with archive.open(info) as handle:
        data = handle.read(remaining + 1)
    if len(data) > remaining:
        raise ParseFailed("MinerU 结果过大")
    return data


def _choose_markdown(files: dict[str, bytes]) -> tuple[str, bytes]:
    """优先使用 content.md，否则选体积最大的 Markdown。"""
    markdown = {
        name: data
        for name, data in files.items()
        if Path(name).suffix.lower() in _MARKDOWN
    }
    if not markdown:
        raise ParseFailed("MinerU 结果没有 Markdown")
    preferred = [
        name for name in markdown if Path(name).name.lower() == "content.md"
    ]
    if len(preferred) == 1:
        name = preferred[0]
    else:
        name = max(markdown, key=lambda item: (len(markdown[item]), item))
    return name, markdown[name]


def _decode_markdown(content: bytes) -> str:
    """按 UTF-8 读取分段正文。"""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseFailed("MinerU Markdown 不是 UTF-8 文本") from exc


def _images_beside(
    markdown_name: str,
    files: dict[str, bytes],
) -> tuple[tuple[str, bytes], ...]:
    """图片路径相对于 Markdown 所在目录，和合并后的链接一致。"""
    parent = Path(markdown_name).parent
    images: list[tuple[str, bytes]] = []
    for name, data in sorted(files.items()):
        if Path(name).suffix.lower() not in _IMAGES:
            continue
        relative = _relative_to_markdown(parent, name)
        images.append((relative, data))
    return tuple(images)


def _relative_to_markdown(parent: Path, name: str) -> str:
    """能放在 Markdown 旁边就保留相对路径，否则只用文件名。"""
    path = Path(name)
    if parent == Path("."):
        return path.as_posix()
    try:
        return path.relative_to(parent).as_posix()
    except ValueError:
        return path.name


def _content_json(markdown_name: str, files: dict[str, bytes]) -> bytes | None:
    """取 Markdown 旁边的 content.json。"""
    sibling = Path(markdown_name).parent / "content.json"
    return files.get(sibling.as_posix())


def _part_objects(
    document_id: str,
    version: int,
    part: _ExtractedPart,
) -> tuple[int, tuple[StoredObject, ...]]:
    """一段的正文、图片和清单。"""
    prefix = f"documents/{document_id}/parse/{version}/parts/{part.part_index}"
    asset_prefix = (
        f"documents/{document_id}/parsed/{version}/assets/part-{part.part_index}"
    )
    objects = [
        StoredObject(
            key=f"{prefix}/content.md",
            body=part.markdown_bytes,
            content_type=_CONTENT_TYPES[".md"],
        )
    ]
    if part.content_json is not None:
        objects.append(
            StoredObject(
                key=f"{prefix}/content.json",
                body=part.content_json,
                content_type=_CONTENT_TYPES[".json"],
            )
        )
    image_keys = []
    for relative, data in part.images:
        key = f"{asset_prefix}/{relative}"
        image_keys.append(key)
        objects.append(
            StoredObject(
                key=key,
                body=data,
                content_type=_content_type(relative),
            )
        )
    manifest_key = f"{prefix}/manifest.json"
    manifest = {
        "part_index": part.part_index,
        "page_start": part.page_start,
        "page_end": part.page_end,
        "markdown_key": f"{prefix}/content.md",
        "images": image_keys,
    }
    objects.append(
        StoredObject(
            key=manifest_key,
            body=_dump(manifest),
            content_type=_CONTENT_TYPES[".json"],
        )
    )
    return part.part_index, tuple(objects)


def _package(
    *,
    document_id: str,
    version: int,
    file_type: str,
    source_key: str,
    merged: str,
    page_count: int,
    now: datetime,
    part_files: tuple[tuple[int, tuple[StoredObject, ...]], ...],
    extra_json: dict[str, object] | None = None,
) -> CollectedParse:
    """组装整份文档的 merged.md、merged.json 和 manifest。"""
    base = f"documents/{document_id}/parsed/{version}"
    md_key = f"{base}/merged.md"
    json_key = f"{base}/merged.json"
    manifest_key = f"{base}/manifest.json"
    part_results = tuple(
        (index, _manifest_key(files))
        for index, files in part_files
        if _manifest_key(files)
    )
    payload = extra_json or {
        "type": file_type,
        "page_count": page_count,
        "parts": [
            {
                "part_index": index,
                "result_key": key,
            }
            for index, key in part_results
        ],
    }
    manifest = {
        "document_id": document_id,
        "file_type": file_type,
        "page_count": page_count,
        "parse_version": version,
        "source_key": source_key,
        "merged_md_key": md_key,
        "merged_json_key": json_key,
        "parts": [
            {"part_index": index, "result_key": key}
            for index, key in part_results
        ],
        "created_at": _timestamp(now),
    }
    objects = [item for _index, files in part_files for item in files]
    objects.extend(
        [
            StoredObject(md_key, merged.encode("utf-8"), _CONTENT_TYPES[".md"]),
            StoredObject(json_key, _dump(payload), _CONTENT_TYPES[".json"]),
            StoredObject(manifest_key, _dump(manifest), _CONTENT_TYPES[".json"]),
        ]
    )
    result = ArtifactKeys(
        md_key=md_key,
        json_key=json_key,
        manifest_key=manifest_key,
        part_results=part_results,
    )
    return CollectedParse(result=result, objects=tuple(objects))


def _page_count(parts: list[_ExtractedPart]) -> int:
    """用各段结束页里的最大值作为总页数。"""
    ends = [part.page_end for part in parts if part.page_end > 0]
    return max(ends) if ends else 0


def _manifest_key(files: tuple[StoredObject, ...]) -> str:
    """找出这段的清单 key。"""
    for item in files:
        if item.key.endswith("/manifest.json"):
            return item.key
    return ""


def _block(block: ParsedBlock) -> dict[str, object]:
    """保留分块里会用到的字段。"""
    item: dict[str, object] = {"kind": block.kind, "text": block.text}
    if block.page is not None:
        item["page"] = block.page
    if block.sheet:
        item["sheet"] = block.sheet
    if block.heading_path:
        item["heading_path"] = block.heading_path
    return item


def _content_type(name: str) -> str:
    """按扩展名选择对象的内容类型。"""
    return _CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _dump(payload: dict[str, object]) -> bytes:
    """写成不转义中文的 JSON。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _timestamp(now: datetime) -> str:
    """清单里的时间用 UTC。"""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
