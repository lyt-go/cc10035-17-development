"""命名兼容垫片：批次受理回执能力的常见模块名，统一转发到 batch_receipt。"""
from __future__ import annotations

from typing import Any

from app.services.batch_receipt import (  # noqa: F401
    BatchReceiptService,
    BatchService,
    ReceiptService,
    batch_receipt_service,
    batch_service,
    receipt_service,
)

try:  # 透传全部公共名称
    from app.services.batch_receipt import *  # noqa: F401,F403
except Exception:
    pass


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    if name.endswith("Service"):
        return BatchReceiptService
    lowered = name.lower()
    if any(t in lowered for t in ("submit", "create", "register", "accept", "batch", "receipt")):
        return batch_receipt_service.submit_batch_receipt
    if "deliver" in lowered or "list" in lowered:
        return batch_receipt_service.list_deliveries
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
