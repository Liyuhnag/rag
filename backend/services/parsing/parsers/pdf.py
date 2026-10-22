# -*- coding: utf-8 -*-
"""PDF 解析器。"""

from services.parsing.adapters.object_reader import StorageObjectReader
from services.parsing.errors import ParseFailed
from services.parsing.mineru import MinerUClient
from services.parsing.mineru import submit_whole_file
from services.parsing.models import ParseInput
from services.parsing.models import ParseSubmission
from services.parsing.parsers.base import BaseParser
from services.parsing.ports import ObjectReader

_MAX_BYTES = 180 * 1024 * 1024


class PdfParser(BaseParser):
    """不超过单次上限的 PDF 直接交给 MinerU。"""

    file_types = ("pdf",)

    def __init__(
        self,
        mineru: MinerUClient | None = None,
        reader: ObjectReader | None = None,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        self._mineru = mineru or MinerUClient()
        self._reader = reader or StorageObjectReader()
        self._max_bytes = max_bytes

    def _parse(self, source: ParseInput) -> ParseSubmission:
        if source.request.size_bytes > self._max_bytes:
            raise ParseFailed("PDF 超过 180MiB，需要先拆分再提交")
        raw = self._reader.read_object(source.request.object_key)
        if len(raw) > self._max_bytes:
            raise ParseFailed("PDF 超过 180MiB，需要先拆分再提交")
        return submit_whole_file(
            self._mineru,
            raw,
            source.request.filename,
            source.request.object_key,
        )
