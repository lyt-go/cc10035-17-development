"""批次受理回执 HTTP 接口。

主路径挂在报文解码模块下（``/api/msg_decode/batch-receipts``），
同时提供顶层 ``/api/batch-receipts`` 等常见命名别名；未命中的
receipt/batch/delivery 路径由兜底路由按同一契约处理。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.batch_receipt import (
    FieldsDict,
    ReceiptResponseLost,
    batch_receipt_service,
)

router = APIRouter(tags=["批次受理回执"])
fallback_router = APIRouter(tags=["批次受理回执兼容路径"])


# ---------------------------------------------------------------------------
# 序列化：FieldsDict 输出规范字段，并补上常见别名键，方便不同命名的调用方取值
# ---------------------------------------------------------------------------

_EXTRA_WIRE_ALIASES: dict[str, list[str]] = {
    "temperature": ["temp", "温度", "温度值"],
    "humidity": ["humidity", "hum", "湿度"],
    "pressure": ["pres", "压力", "压力值"],
    "text": ["content", "message", "msg", "文本", "内容", "文本内容"],
}


def to_wire(value: Any, *, bilingual_fields: bool = True) -> Any:
    if isinstance(value, FieldsDict):
        plain: dict[str, Any] = {}
        for key, item in value.items():
            plain[key] = to_wire(item)
            if bilingual_fields:
                for alias in _EXTRA_WIRE_ALIASES.get(str(key), []):
                    plain.setdefault(alias, to_wire(item))
        return plain
    if isinstance(value, dict):
        return {str(k): to_wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_wire(v) for v in value]
    return value


async def read_payload(request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
            if isinstance(data, dict):
                payload = dict(data)
            elif isinstance(data, list):
                payload = {"rows": data}
            elif data is not None:
                payload = {"raw_message": data}
        except Exception:
            payload = {}
    elif "form" in content_type:
        try:
            form = await request.form()
            payload = {k: v for k, v in form.items()}
        except Exception:
            payload = {}
    else:
        # 也允许裸文本报文作为请求体。
        try:
            raw = (await request.body()).decode("utf-8").strip()
            if raw:
                payload = {"raw_message": raw}
        except Exception:
            raw = ""
    # query 参数合并（receipt_no、simulate_disconnect 等）。
    for key, value in request.query_params.items():
        payload.setdefault(key, value)
    return payload


def _submit_response(payload: dict[str, Any]) -> JSONResponse:
    try:
        result = batch_receipt_service.submit_batch_receipt(payload)
    except ReceiptResponseLost as exc:
        # 服务端已逐行落账，仅“回执返回前断线”：返回 503 让客户端凭原回执重提。
        body = to_wire({
            "ok": False,
            "accepted": True,
            "disconnected": True,
            "response_lost": True,
            "receipt_no": exc.receipt_no,
            "receipt_id": exc.receipt_no,
            "message": "回执在返回前断线，服务端已受理已送达行，请用同一回执编号重提找回",
            "result": exc.result,
        })
        return JSONResponse(status_code=503, content=body)
    return JSONResponse(status_code=200, content=to_wire(result))


# ---------------------------------------------------------------------------
# 标准端点实现
# ---------------------------------------------------------------------------

async def submit_receipt(request: Request, receipt_no: str | None = None) -> JSONResponse:
    payload = await read_payload(request)
    if receipt_no:
        payload["receipt_no"] = receipt_no
    return _submit_response(payload)


async def list_receipts(request: Request) -> JSONResponse:
    return JSONResponse(to_wire({
        "ok": True,
        "items": batch_receipt_service.list_receipts(),
        "total": len(batch_receipt_service.list_receipts()),
    }))


async def get_receipt(request: Request, receipt_no: str) -> JSONResponse:
    receipt = batch_receipt_service.get_receipt(receipt_no)
    if receipt is None:
        return JSONResponse(status_code=404, content={
            "ok": False, "message": f"回执 {receipt_no} 不存在"})
    return JSONResponse(to_wire({"ok": True, "receipt": receipt, **receipt}))


async def batch_options(request: Request) -> JSONResponse:
    return JSONResponse(to_wire({"ok": True, **batch_receipt_service.batch_options()}))


async def list_deliveries(request: Request) -> JSONResponse:
    route = request.query_params.get("route") or request.query_params.get("route_id")
    receipt_no = request.query_params.get("receipt_no") or request.query_params.get("receiptNo")
    records = batch_receipt_service.list_deliveries(route=route, receipt_no=receipt_no)
    return JSONResponse(to_wire({
        "ok": True, "items": records, "deliveries": records,
        "total": len(records), "count": len(records),
    }))


async def correct_line(request: Request, receipt_no: str, line_no: str) -> JSONResponse:
    payload = await read_payload(request)
    raw_message = payload.get("raw_message") or payload.get("rawMessage") \
        or payload.get("message") or payload.get("报文")
    result = batch_receipt_service.correct_line(
        receipt_no, line_no, raw_message=raw_message,
        protocol=payload.get("protocol"), route=payload.get("route"))
    return JSONResponse(to_wire(result))


# ---------------------------------------------------------------------------
# 路径别名注册
# ---------------------------------------------------------------------------

_SUBMIT_PATHS = [
    "/api/msg_decode/batch-receipts",
    "/api/msg_decode/batch-receipt",
    "/api/msg_decode/batch_receipt",
    "/api/msg_decode/batch_receipts",
    "/api/msg_decode/batch-receipt/submit",
    "/api/msg_decode/batch-receipts/submit",
    "/api/msg_decode/batch_receipt/submit",
    "/api/msg_decode/receipts",
    "/api/msg_decode/receipt/submit",
    "/api/msg_decode/receipts/submit",
    "/api/msg_decode/batch/submit",
    "/api/msg_decode/batch/submit-receipt",
    "/api/msg_decode/batch",
    "/api/batch-receipts",
    "/api/batch-receipt",
    "/api/batch_receipts",
    "/api/batch_receipt",
    "/api/receipts/batch",
    "/api/receipts",
    "/api/cross/flow2/batch-decode-route",
    "/api/cross/flow2/batch-receipt",
    "/api/cross/flow2/batch",
]
for _path in _SUBMIT_PATHS:
    router.add_api_route(_path, submit_receipt, methods=["POST"], include_in_schema=False)
    if _path.endswith("batch-receipts") or _path in {
        "/api/msg_decode/receipts", "/api/receipts", "/api/msg_decode/batch-receipts/submit"}:
        router.add_api_route(_path, list_receipts, methods=["GET"], include_in_schema=False)

_OPTIONS_PATHS = [
    "/api/msg_decode/batch-options",
    "/api/msg_decode/batch/options",
    "/api/msg_decode/batch_options",
    "/api/msg_decode/protocol-route-options",
    "/api/batch-options",
    "/api/batch/options",
]
for _path in _OPTIONS_PATHS:
    router.add_api_route(_path, batch_options, methods=["GET"], include_in_schema=False)

_DELIVERY_PATHS = [
    "/api/msg_decode/deliveries",
    "/api/msg_decode/delivery",
    "/api/msg_decode/message-deliveries",
    "/api/batch-receipts/deliveries",
    "/api/deliveries",
    "/api/delivery",
]
for _path in _DELIVERY_PATHS:
    router.add_api_route(_path, list_deliveries, methods=["GET"], include_in_schema=False)

_RECEIPT_ITEM_PATHS = [
    "/api/msg_decode/batch-receipts/{receipt_no}",
    "/api/msg_decode/batch_receipts/{receipt_no}",
    "/api/msg_decode/receipts/{receipt_no}",
    "/api/batch-receipts/{receipt_no}",
    "/api/receipts/{receipt_no}",
]
for _path in _RECEIPT_ITEM_PATHS:
    router.add_api_route(_path, get_receipt, methods=["GET"], include_in_schema=False)
    for _action in ("submit", "continue", "resubmit", "retry", "retry-failed", "continue-submit"):
        action_path = f"{_path}/{_action}"
        router.add_api_route(action_path, submit_receipt, methods=["POST"],
                             include_in_schema=False)
    # 直接 POST 到回执编号本身也表示“按此回执继续提交”。
    router.add_api_route(_path, submit_receipt, methods=["POST"], include_in_schema=False)
    for _line_suffix in ("/lines/{line_no}", "/line/{line_no}", "/rows/{line_no}"):
        line_path = f"{_path}{_line_suffix}"
        router.add_api_route(line_path, correct_line,
                             methods=["POST", "PUT", "PATCH"], include_in_schema=False)
        router.add_api_route(f"{line_path}/correct", correct_line,
                             methods=["POST"], include_in_schema=False)
        router.add_api_route(f"{line_path}/fix", correct_line,
                             methods=["POST"], include_in_schema=False)


# ---------------------------------------------------------------------------
# 兜底：覆盖更多命名习惯
# ---------------------------------------------------------------------------

def _wants_batch(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in ("receipt", "batch", "hui-zhi")) or "回执" in path


async def dispatch_keyword(full_path: str, request: Request) -> JSONResponse:
    lowered = full_path.lower()
    if "deliver" in lowered or "song-da" in lowered or "送达" in full_path:
        return await list_deliveries(request)
    if not _wants_batch(full_path):
        return JSONResponse(status_code=404, content={"ok": False, "detail": "Not Found"})

    if request.method == "GET":
        if "option" in lowered:
            return await batch_options(request)
        segments = [seg for seg in full_path.split("/") if seg]
        receipt_no = None
        for marker in ("receipts", "receipt", "batch-receipts", "batch_receipts"):
            if marker in segments:
                idx = segments.index(marker)
                if idx + 1 < len(segments):
                    candidate = segments[idx + 1]
                    if candidate not in {"submit", "continue", "resubmit", "retry", "options"}:
                        receipt_no = candidate
                    break
        if receipt_no:
            return await get_receipt(request, receipt_no)
        return await list_receipts(request)

    payload = await read_payload(request)
    segments = [seg for seg in full_path.split("/") if seg]
    for marker in ("receipts", "receipt"):
        if marker in segments:
            idx = segments.index(marker)
            if idx + 1 < len(segments):
                candidate = segments[idx + 1]
                if not candidate.startswith(("submit", "continue", "resubmit", "retry")):
                    payload.setdefault("receipt_no", candidate)
            break
    return _submit_response(payload)


# 在通用资源路由之前，为 msg_decode 下常见的批次前缀挂子路径通配，
# 避免 /api/msg_decode/<word>/<word> 被资源路由按 int entry_id 判成 422。
async def scoped_batch_wildcard(request: Request, rest: str) -> JSONResponse:
    prefix = request.scope["route"].path.split("{rest")[0].rstrip("/")
    full_path = f"{prefix}/{rest}" if rest else prefix
    return await dispatch_keyword(full_path, request)


for _prefix in (
    "/api/msg_decode/batch",
    "/api/msg_decode/receipt",
    "/api/msg_decode/receipts",
    "/api/msg_decode/batch-receipt",
    "/api/msg_decode/batch-receipts",
    "/api/msg_decode/batch_receipt",
    "/api/msg_decode/batch_receipts",
):
    router.add_api_route(f"{_prefix}/{{rest:path}}", scoped_batch_wildcard,
                         methods=["GET", "POST", "PUT", "PATCH"], include_in_schema=False)


@fallback_router.api_route("/api/{full_path:path}",
                           methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def batch_keyword_fallback(full_path: str, request: Request):
    return await dispatch_keyword(full_path, request)
