# -*- coding: utf-8 -*-
"""解析器抽象基类。

使用模板方法：
- `parse()` 固定解析器入口和文件类型校验；
- 子类只实现 `_parse()`；
- 具体解析逻辑由后续功能实现。
"""

from abc import ABC
from abc import abstractmethod

from services.parsing.errors import UnsupportedFileType
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument


class BaseParser(ABC):
    """所有文件解析器的基类。"""

    file_types: tuple[str, ...] = ()

    def supports(self, file_type: str) -> bool:
        """当前解析器是否支持该扩展名。"""
        return file_type in self.file_types

    def parse(self, source: ParseInput) -> ParsedDocument:
        """解析入口；具体格式处理交给子类。"""
        if not self.supports(source.request.file_type):
            raise UnsupportedFileType(
                f"解析器不支持文件类型: {source.request.file_type}"
            )
        return self._parse(source)

    @abstractmethod
    def _parse(self, source: ParseInput) -> ParsedDocument:
        """子类实现具体解析逻辑。"""
        ...
