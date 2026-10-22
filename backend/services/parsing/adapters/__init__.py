# -*- coding: utf-8 -*-
"""解析模块的外部服务适配器。"""

from services.parsing.adapters.libreoffice import LibreOfficeConverter
from services.parsing.adapters.mineru import MinerUClient

__all__ = ["LibreOfficeConverter", "MinerUClient"]
