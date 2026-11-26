"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import batch_receipt, cross_module, resource

# 兼容旧代码：ROUTERS 仍是带 .router 的模块列表。
ROUTERS = [cross_module, batch_receipt, resource]

# 实际注册顺序：
# 1) cross 固定前缀；2) 批次回执固定路径（必须在通用 /{module}/{entry_id} 前）；
# 3) 通用资源路由；4) 批次相关兜底路径（必须最后注册）。
ROUTER_OBJECTS = [
    cross_module.router,
    batch_receipt.router,
    resource.router,
    batch_receipt.fallback_router,
]
