# -*- coding: utf-8 -*-
"""DOCX 解析器。"""

from services.indexing.parsing.adapters.object_reader import StorageObjectReader
from services.indexing.parsing.mineru import MinerUClient
from services.indexing.parsing.models import ParseInput
from services.indexing.parsing.models import ParseSubmission
from services.indexing.parsing.parsers.base import BaseParser
from services.indexing.parsing.parsers.office import submit_office_file
from services.indexing.parsing.ports import ObjectReader

_MAX_BYTES = 180 * 1024 * 1024


class DocxParser(BaseParser):
    """DOCX 下载后交给 MinerU。超过单次上限则失败。"""

    file_types = ("docx",)

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
        return submit_office_file(
            self._mineru,
            self._reader,
            source,
            self._max_bytes,
        )
