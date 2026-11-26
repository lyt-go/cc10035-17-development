"""顶层命名兼容垫片：转发到 app.services.batch_receipt。"""
from __future__ import annotations

from app.services.batch_receipt import *  # noqa: F401,F403
from app.services.batch_receipt import (  # noqa: F401
    BatchReceiptService,
    batch_receipt_service,
)


def __getattr__(name):
    import app.services.batch_receipt as core
    return getattr(core, name)
