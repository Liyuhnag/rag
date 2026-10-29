# -*- coding: utf-8 -*-
"""收集 MinerU 分段结果，合并后生成要写入对象存储的文件。"""

import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

from services.parsing.archive import check_archive_directory
from services.parsing.errors import ParseFailed

_MAX_FILES = 10_000
_MAX_BYTES = 1024 * 1024 * 1024
_MAX_ZIP_BYTES = 2 * 1024 * 1024 * 1024
_MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
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
    result_key: str


@dataclass(frozen=True)
class ArtifactKeys:
    """合并产物在对象存储中的位置。"""

    md_key: str
    json_key: str
    manifest_key: str
    part_results: tuple[tuple[int, str], ...] = ()


def collect_parts(
    document_id: str,
    version: int,
    file_type: str,
    source_key: str,
    parts: tuple[PartZip, ...] | list[PartZip],
    now: datetime,
    reader,
) -> ArtifactKeys:
    """逐段落盘、验证后上传；总清单在其他产物完成后发布。"""
    if not parts:
        raise ParseFailed("没有可合并的解析结果")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        staged = []
        part_results = []
        sections = []
        for n, part in enumerate(parts):
            archive_path = root / f"part-{n}.zip"
            reader.download_object(part.result_key, archive_path)
            section, objects, manifest_key = _stage_part(
                document_id, version, part, archive_path, root, n
            )
            archive_path.unlink()
            staged.extend(objects)
            sections.append((part.page_start, part.part_index, section))
            part_results.append((part.part_index, manifest_key))
        base = f"documents/{document_id}/parsed/{version}"
        md_key = f"{base}/merged.md"
        json_key = f"{base}/merged.json"
        manifest_key = f"{base}/manifest.json"
        merged_path = root / "merged.md"
        with merged_path.open("wb") as target:
            written = False
            for _, _, section in sorted(sections):
                if section.stat().st_size == 0:
                    continue
                if written:
                    target.write(b"\n\n")
                with section.open("rb") as source:
                    _copy_bounded(source, target)
                written = True
            if written:
                target.write(b"\n")
        page_count = max((part.page_end for part in parts), default=0)
        page_count = max(page_count, 0)
        entries = [
            {"part_index": index, "result_key": key} for index, key in part_results
        ]
        payload = {"type": file_type, "page_count": page_count, "parts": entries}
        manifest = {
            "document_id": document_id,
            "file_type": file_type,
            "page_count": page_count,
            "parse_version": version,
            "source_key": source_key,
            "merged_md_key": md_key,
            "merged_json_key": json_key,
            "parts": entries,
            "created_at": _timestamp(now),
        }
        for key, path, content_type in staged:
            reader.upload_file(key, path, content_type)
        reader.upload_file(md_key, merged_path, _CONTENT_TYPES[".md"])
        reader.write_object(json_key, _dump(payload), _CONTENT_TYPES[".json"])
        reader.write_object(manifest_key, _dump(manifest), _CONTENT_TYPES[".json"])
        return ArtifactKeys(md_key, json_key, manifest_key, tuple(part_results))


def _copy_bounded(source, target, remaining=None):
    total = 0
    while True:
        size = 65536 if remaining is None else min(65536, remaining - total + 1)
        data = source.read(size)
        if not data:
            return total
        total += len(data)
        if remaining is not None and total > remaining:
            raise ParseFailed("MinerU 结果过大")
        target.write(data)


def _check_archive_directory(path: Path) -> None:
    """在构建 ZIP 索引前限制目录内存。"""
    check_archive_directory(
        path,
        label="MinerU",
        max_bytes=_MAX_ZIP_BYTES,
        max_files=_MAX_FILES,
        max_directory_bytes=_MAX_DIRECTORY_BYTES,
    )


def _stage_part(document_id, version, part, archive_path, root, ordinal):
    """校验实际解压字节并落盘，绝不信任 ZIP 声明大小。"""
    _check_archive_directory(archive_path)
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParseFailed("MinerU 结果不是有效的压缩包") from exc
    files = {}
    total = 0
    with archive:
        if len(archive.infolist()) > _MAX_FILES:
            raise ParseFailed("MinerU 结果文件过多")
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)
            if Path(name).suffix.lower() not in _ALLOWED:
                continue
            if name in files:
                raise ParseFailed("MinerU 结果包含重复文件")
            if len(files) >= _MAX_FILES:
                raise ParseFailed("MinerU 结果文件过多")
            path = root / f"member-{ordinal}-{len(files)}"
            try:
                with archive.open(info) as source, path.open("wb") as target:
                    size = _copy_bounded(source, target, _MAX_BYTES - total)
            except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
                raise ParseFailed("MinerU 压缩包内容无效") from exc
            total += size
            files[name] = (path, size)
    markdown = [name for name in files if Path(name).suffix.lower() in _MARKDOWN]
    if not markdown:
        raise ParseFailed("MinerU 结果没有 Markdown")
    preferred = [name for name in markdown if Path(name).name.lower() == "full.md"]
    if not preferred:
        preferred = [
            name for name in markdown if Path(name).name.lower() == "content.md"
        ]
    name = (
        preferred[0]
        if len(preferred) == 1
        else max(markdown, key=lambda x: (files[x][1], x))
    )
    prefix = f"documents/{document_id}/parse/{version}/parts/{part.part_index}"
    assets = f"documents/{document_id}/parsed/{version}/assets/part-{part.part_index}"
    objects = [(f"{prefix}/content.md", files[name][0], _CONTENT_TYPES[".md"])]
    # 官方 ZIP 使用 *_content_list.json/layout.json；保留所有 JSON，避免丢失结构化结果。
    json_keys = []
    for json_name in sorted(files):
        if Path(json_name).suffix.lower() != ".json":
            continue
        key = f"{prefix}/json/{json_name}"
        json_keys.append(key)
        objects.append((key, files[json_name][0], _CONTENT_TYPES[".json"]))
    sibling = (Path(name).parent / "content.json").as_posix()
    if sibling in files:
        objects.append(
            (f"{prefix}/content.json", files[sibling][0], _CONTENT_TYPES[".json"])
        )
    image_keys = []
    for image in sorted(files):
        if Path(image).suffix.lower() not in _IMAGES:
            continue
        relative = _relative_to_markdown(Path(name).parent, image)
        key = f"{assets}/{relative}"
        image_keys.append(key)
        objects.append((key, files[image][0], _content_type(relative)))
    part_manifest = root / f"manifest-{ordinal}.json"
    part_manifest.write_bytes(
        _dump(
            {
                "part_index": part.part_index,
                "page_start": part.page_start,
                "page_end": part.page_end,
                "markdown_key": f"{prefix}/content.md",
                "images": image_keys,
                "json_keys": json_keys,
            }
        )
    )
    manifest_key = f"{prefix}/manifest.json"
    objects.append((manifest_key, part_manifest, _CONTENT_TYPES[".json"]))
    section = root / f"section-{ordinal}.md"
    _render_stream(files[name][0], section, part)
    return section, objects, manifest_key


def _render_stream(source_path, target_path, part):
    """有限缓冲改写图片链接；超长未完成的链接明确拒绝。"""
    from services.parsing.merge import _IMAGE
    from services.parsing.merge import _rewrite_images

    limit = 1024 * 1024
    marker = f"<!-- page: {part.page_start if part.page_start > 0 else 1} -->"
    body = target_path.with_suffix(".body")
    try:
        with source_path.open("r", encoding="utf-8-sig", newline="") as source:
            with body.open("w", encoding="utf-8", newline="") as output:
                pending = ""
                started = False
                while True:
                    chunk = source.read(65536)
                    eof = not chunk
                    if not started:
                        chunk = chunk.lstrip()
                        started = bool(chunk)
                    pending += chunk
                    while pending:
                        match = _IMAGE.search(pending)
                        if match:
                            output.write(pending[: match.start()])
                            output.write(
                                _rewrite_images(match.group(0), part.part_index)
                            )
                            pending = pending[match.end() :]
                            continue
                        start = pending.find("![")
                        if start < 0:
                            keep = 1 if pending.endswith("!") and chunk else 0
                            output.write(pending[:-keep] if keep else pending)
                            pending = pending[-keep:] if keep else ""
                        elif start:
                            output.write(pending[:start])
                            pending = pending[start:]
                        if len(pending) > limit:
                            raise ParseFailed("MinerU Markdown 图片链接过长")
                        break
                    if eof:
                        output.write(pending)
                        break
    except UnicodeDecodeError as exc:
        raise ParseFailed("MinerU Markdown 不是 UTF-8 文本") from exc
    _strip_file(body)
    with target_path.open("wb") as target:
        if body.stat().st_size:
            with body.open("rb") as text:
                prefix = text.read(len(marker.encode()))
                text.seek(0)
                if prefix != marker.encode():
                    target.write((marker + "\n\n").encode())
                _copy_bounded(text, target)
        elif part.page_start > 0:
            target.write(marker.encode())


def _strip_file(path):
    """以有限内存按 UTF-8 字符剔除首尾空白。"""
    # 首部已在行读取时剔除；尾部通过逆向读取限定大小块查找。
    with path.open("r+b") as handle:
        end = handle.seek(0, 2)
        # 反向以字符边界为单位扫描；单字符至多四字节。
        while end:
            start = max(0, end - 65536)
            handle.seek(start)
            block = handle.read(end - start)
            if start:
                while block and block[0] & 0xC0 == 0x80:
                    start += 1
                    block = block[1:]
            text = block.decode("utf-8")
            trimmed = text.rstrip()
            if trimmed:
                end = start + len(trimmed.encode("utf-8"))
                break
            end = start
        handle.truncate(end)


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


def _relative_to_markdown(parent: Path, name: str) -> str:
    """能放在 Markdown 旁边就保留相对路径，否则只用文件名。"""
    path = Path(name)
    if parent == Path("."):
        return path.as_posix()
    try:
        return path.relative_to(parent).as_posix()
    except ValueError:
        return path.name


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
