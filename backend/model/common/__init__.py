# -*- coding: utf-8 -*-
"""各模型共用的数据库配置。"""

from model.common.database import as_aware
from model.common.database import as_utc
from model.common.database import configure
from model.common.database import execute

__all__ = ["as_aware", "as_utc", "configure", "execute"]
