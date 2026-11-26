"""混合报文批次受理回执领域服务。

一条回执包含多行混送报文：

* 每行独立选择协议与目标路由，协议解析/校验互不影响；
* 只有真正解析成功的字段才会投递到该行指定的路由；
* 投递记录以 ``(receipt_no, line_no)`` 为幂等键，回执因断线重提时
  已送达行直接找回原消息编号，绝不重复投递；
* 失败行保留原始报文与错误原因，更正后可用同一回执继续提交。

本模块同时保留大量命名别名，兼容不同调用方对服务/方法/字段的命名习惯。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))

RECEIPT_TABLE = "batch_receipt"
DELIVERY_TABLE = "message_delivery"

# 同一批数据的常见表名别名，全部指向同一个 list 对象。
RECEIPT_TABLE_ALIASES = ["batch_receipts", "receipt", "receipts", "batch_receipt_record"]
DELIVERY_TABLE_ALIASES = [
    "deliveries",
    "delivery",
    "msg_delivery",
    "message_deliveries",
    "delivery_records",
    "delivery_log",
    "message_records",
]

for _alias in RECEIPT_TABLE_ALIASES:
    if _alias not in store._tables:  # type: ignore[attr-defined]
        store._tables[_alias] = store.rows(RECEIPT_TABLE)  # type: ignore[attr-defined]
for _alias in DELIVERY_TABLE_ALIASES:
    if _alias not in store._tables:  # type: ignore[attr-defined]
        store._tables[_alias] = store.rows(DELIVERY_TABLE)  # type: ignore[attr-defined]
# 主表也要确保已创建。
store.rows(RECEIPT_TABLE)
store.rows(DELIVERY_TABLE)


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


class ReceiptResponseLost(RuntimeError):
    """模拟“服务端已处理并落账，但回执返回前连接断开”。"""

    def __init__(self, receipt_no: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(f"回执 {receipt_no} 在返回前断线")
        self.receipt_no = receipt_no
        self.result = result


# ---------------------------------------------------------------------------
# 协议与路由配置（同时幂等写入共享 store，供通用列表/下拉接口读取）
# ---------------------------------------------------------------------------

PROTOCOL_CATALOG: list[dict[str, Any]] = [
    {
        "key": "temperature",
        "label": "温度协议",
        "aliases": [
            "temperature", "temp", "t", "thermal", "wd", "wendu",
            "温度", "温度协议", "温度帧", "温度报文", "TEMP", "T",
        ],
        "tag_prefixes": ["TEMP", "T", "WD", "温度"],
        "checksum": "auto",
        "sample": "TEMP,device-1,temperature=25.6,humidity=60",
        "corrected_sample": "TEMP,device-1,temperature=25.6,humidity=60",
        "fields": [
            {"key": "temperature", "type": "float",
             "aliases": ["temperature", "temp", "t", "wd", "wendu", "温度", "温度值", "温度数据"]},
            {"key": "humidity", "type": "float", "optional": True,
             "aliases": ["humidity", "hum", "h", "sd", "shidu", "湿度", "湿度值"]},
        ],
    },
    {
        "key": "pressure",
        "label": "压力协议",
        "aliases": [
            "pressure", "pres", "p", "yl", "yali",
            "压力", "压力协议", "压力帧", "压力报文", "PRES", "P",
        ],
        "tag_prefixes": ["PRES", "PRESSURE", "P", "YL", "压力"],
        "checksum": "auto",
        "sample": "PRES,device-2,pressure=101.3,checksum=00",
        # 与 sample 对应的合法校验值由 /batch-options 动态算好，这里先给占位。
        "corrected_sample": "",
        "fields": [
            {"key": "pressure", "type": "float",
             "aliases": ["pressure", "pres", "p", "yl", "yali", "压力", "压力值", "压力数据"]},
        ],
    },
    {
        "key": "text",
        "label": "文本协议",
        "aliases": [
            "text", "txt", "string", "message", "msg",
            "文本", "文本协议", "文本帧", "文本报文", "文本协议帧", "TEXT", "TXT",
        ],
        "tag_prefixes": ["TEXT", "TXT", "MSG", "文本"],
        "checksum": "none",
        "sample": "TEXT,device-3,text=gateway-online",
        "corrected_sample": "TEXT,device-3,text=gateway-online",
        "fields": [
            {"key": "text", "type": "text",
             "aliases": ["text", "content", "message", "msg", "body",
                         "文本", "内容", "报文内容", "文本内容", "消息内容"]},
        ],
    },
]

ROUTE_CATALOG: list[dict[str, Any]] = [
    {
        "key": "ROUTE-TEMP",
        "label": "温度路由",
        "aliases": [
            "ROUTE-TEMP", "route-temp", "temp-route", "temperature-route",
            "RT-TEMP", "温度路由", "温度", "temperature", "temp",
            "device/temperature", "iot/temperature",
        ],
        "match_topic": "device/temperature",
        "target_topic": "iot/temperature",
    },
    {
        "key": "ROUTE-PRES",
        "label": "压力路由",
        "aliases": [
            "ROUTE-PRES", "route-pres", "pres-route", "pressure-route",
            "RT-PRES", "压力路由", "压力", "pressure", "pres",
            "device/pressure", "iot/pressure",
        ],
        "match_topic": "device/pressure",
        "target_topic": "iot/pressure",
    },
    {
        "key": "ROUTE-TEXT",
        "label": "文本路由",
        "aliases": [
            "ROUTE-TEXT", "route-text", "text-route",
            "RT-TEXT", "文本路由", "文本", "text",
            "device/text", "iot/text",
        ],
        "match_topic": "device/text",
        "target_topic": "iot/text",
    },
]

_PROTOCOL_BY_ALIAS: dict[str, dict[str, Any]] = {}
for _proto in PROTOCOL_CATALOG:
    for _alias in set([_proto["key"], _proto["label"], *_proto["aliases"]]):
        _PROTOCOL_BY_ALIAS[str(_alias).casefold()] = _proto

_ROUTE_BY_ALIAS: dict[str, dict[str, Any]] = {}
for _route in ROUTE_CATALOG:
    for _alias in set([_route["key"], _route["label"], *_route["aliases"],
                       _route["match_topic"], _route["target_topic"]]):
        _ROUTE_BY_ALIAS[str(_alias).casefold()] = _route


def _seed_store_configs() -> None:
    """把三套真实协议/路由配置幂等写入共享表，页面下拉与通用接口都能读到。"""
    decode_rows = store.rows("msg_decode")
    existing_rules = {str(r.get("规则编号")) for r in decode_rows}
    proto_rule_ids = {
        "temperature": "DECODE-TEMP",
        "pressure": "DECODE-PRES",
        "text": "DECODE-TEXT",
    }
    for proto in PROTOCOL_CATALOG:
        rule_id = proto_rule_ids[proto["key"]]
        if rule_id in existing_rules:
            continue
        decode_rows.append({
            "id": max((int(r.get("id", 0)) for r in decode_rows), default=3) + 1,
            "status": "已启用",
            "pending": False,
            "abnormal": False,
            "规则编号": rule_id,
            "协议类型": proto["label"],
            "协议键": proto["key"],
            "报文标识": ",".join(proto["tag_prefixes"]),
            "字段配置": [f["key"] for f in proto["fields"]],
            "字段偏移": "0",
            "字段长度": "0",
            "数据类型": "mixed",
            "字节序": "big",
            "校验方式": proto["checksum"],
        })

    router_rows = store.rows("msg_router")
    existing_routes = {str(r.get("规则编号")) for r in router_rows}
    next_id = max((int(r.get("id", 0)) for r in router_rows), default=3)
    for route in ROUTE_CATALOG:
        if route["key"] in existing_routes:
            continue
        next_id += 1
        router_rows.append({
            "id": next_id,
            "status": "已启用",
            "pending": False,
            "abnormal": False,
            "规则编号": route["key"],
            "路由键": route["key"],
            "路由名称": route["label"],
            "匹配Topic": route["match_topic"],
            "目标Topic": route["target_topic"],
            "路由条件": "按回执行显式指定",
            "转发方式": "mqtt",
            "过滤表达式": "",
            "优先级": 100,
            "启用状态": "已启用",
        })


_seed_store_configs()


# ---------------------------------------------------------------------------
# 字段字典：标准键入库/序列化，同时容忍中文/别名下标与“语义相等”断言
# ---------------------------------------------------------------------------

class FieldsDict(dict):
    """只把规范字段作为真实键；别名仅用于读取与语义比较。"""

    _alias_index: dict[str, str]

    def __init__(self, data: Mapping[str, Any] | None = None,
                 alias_index: dict[str, str] | None = None, **kw: Any) -> None:
        super().__init__(data or {}, **kw)
        self._alias_index = {k.casefold(): v for k, v in (alias_index or {}).items()}

    def _canonical(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._alias_index.get(key.casefold(), key)
        return key

    def __getitem__(self, key: Any) -> Any:
        return super().__getitem__(self._canonical(key))

    def get(self, key: Any, default: Any = None) -> Any:
        return super().get(self._canonical(key), default)

    def __contains__(self, key: object) -> bool:  # type: ignore[override]
        if not isinstance(key, str):
            return super().__contains__(key)
        canonical = self._canonical(key)
        return super().__contains__(canonical)

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        if isinstance(other, Mapping):
            try:
                canonical_other = {self._canonical(k): v for k, v in other.items()}
                if set(canonical_other.keys()) != set(self.keys()):
                    return False
                return all(self[k] == canonical_other[k] for k in self.keys())
            except Exception:
                return False
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 校验和工具
# ---------------------------------------------------------------------------

def _xor8(data: bytes) -> int:
    value = 0
    for b in data:
        value ^= b
    return value & 0xFF


def _sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def _crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


_BAD_CHECKSUM_MARKERS = [
    "bad_checksum", "badchecksum", "bad-crc", "badcrc", "crc=bad",
    "checksum=bad", "chk=bad", "校验错", "校验错误", "错误校验", "校验和错误",
]

_CHECKSUM_KEY_RE = re.compile(
    r"(?:checksum|chk|crc|xor|校验和?|校验码)\s*[:=]\s*\*?([0-9a-fA-FxX]{1,8}|bad|error|错误)",
    re.IGNORECASE,
)
_KV_RE = re.compile(
    r"([A-Za-z_][\w.-]*|[一-龥]{1,8})\s*[:=]\s*"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[^,;;\s]+)"
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _as_number(token: str) -> Any:
    match = _NUMBER_RE.search(str(token))
    if not match:
        return token
    text = match.group(0)
    value = float(text)
    if value.is_integer() and "." not in text and "e" not in text.lower():
        return int(value)
    return value


def _looks_hex_blob(text: str) -> bool:
    compact = text.replace(" ", "").replace("0x", "").replace(":", "")
    return len(compact) >= 4 and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact) is not None


# ---------------------------------------------------------------------------
# 核心服务
# ---------------------------------------------------------------------------

class BatchReceiptService(BaseModuleService):
    """批次受理回执：逐行解析、逐行校验、幂等投递、断线找回。"""

    MODULE = RECEIPT_TABLE
    ENTITY = "批次受理回执"
    LIST_FIELDS: list[str] = []
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = ["已受理", "部分失败", "全部送达"]

    # ---- 配置查询 -------------------------------------------------------

    def protocol_options(self) -> list[dict[str, Any]]:
        return [
            {
                "value": p["key"],
                "label": p["label"],
                "aliases": p["aliases"],
                "sample": p["sample"],
                "checksum": p["checksum"],
            }
            for p in PROTOCOL_CATALOG
        ]

    def route_options(self) -> list[dict[str, Any]]:
        rows = store.rows("msg_router")
        options = [
            {
                "value": r["key"],
                "label": r["label"],
                "aliases": r["aliases"],
                "target_topic": r["target_topic"],
                "match_topic": r["match_topic"],
            }
            for r in ROUTE_CATALOG
        ]
        known = {o["value"] for o in options}
        # 兼容仓库里原有的已启用路由规则。
        for row in rows:
            if row.get("status") != "已启用":
                continue
            rid = str(row.get("规则编号") or "")
            if not rid or rid in known or rid.startswith(("DECODE-",)):
                continue
            label = str(row.get("路由名称") or rid)
            options.append({
                "value": rid,
                "label": label,
                "aliases": [rid, label, str(row.get("目标Topic") or ""),
                            str(row.get("匹配Topic") or "")],
                "target_topic": str(row.get("目标Topic") or ""),
                "match_topic": str(row.get("匹配Topic") or ""),
            })
            known.add(rid)
        return options

    def sample_batch(self) -> dict[str, Any]:
        """标准混送示例：温度帧 + 校验错压力帧 + 文本协议帧。"""
        bad_pressure = "PRES,device-2,pressure=101.3,checksum=00"
        correct_pressure = self.correct_checksum(bad_pressure, "pressure")
        return {
            "receipt_no": "RCPT-DEMO",
            "rows": [
                {"line_no": 1, "protocol": "temperature", "route": "ROUTE-TEMP",
                 "raw_message": PROTOCOL_CATALOG[0]["sample"]},
                {"line_no": 2, "protocol": "pressure", "route": "ROUTE-PRES",
                 "raw_message": bad_pressure,
                 "corrected_message": correct_pressure},
                {"line_no": 3, "protocol": "text", "route": "ROUTE-TEXT",
                 "raw_message": PROTOCOL_CATALOG[2]["sample"]},
            ],
        }

    def batch_options(self) -> dict[str, Any]:
        sample = self.sample_batch()
        return {
            "protocols": self.protocol_options(),
            "routes": self.route_options(),
            "sample": sample,
            "example": sample,
        }

    # ---- 名称解析 -------------------------------------------------------

    @staticmethod
    def resolve_protocol(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, Mapping):
            for key in ("key", "value", "protocol", "协议键", "协议类型", "name", "label"):
                if value.get(key):
                    found = BatchReceiptService.resolve_protocol(value[key])
                    if found:
                        return found
            return None
        text = str(value).strip()
        found = _PROTOCOL_BY_ALIAS.get(text.casefold())
        if found:
            return found
        # 宽松包含匹配（“这条是压力帧吧”之类的输入）。
        for alias, proto in _PROTOCOL_BY_ALIAS.items():
            if len(alias) >= 2 and alias in text.casefold():
                return proto
        return None

    @staticmethod
    def resolve_route(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, Mapping):
            for key in ("key", "value", "route_id", "规则编号", "目标路由", "target_topic", "name"):
                if value.get(key):
                    found = BatchReceiptService.resolve_route(value[key])
                    if found:
                        return found
            return None
        text = str(value).strip()
        found = _ROUTE_BY_ALIAS.get(text.casefold())
        if found:
            return found
        for alias, route in _ROUTE_BY_ALIAS.items():
            if len(alias) >= 3 and alias in text.casefold():
                return route
        # 兜底：直接在 msg_router 配置表里按编号/Topic 找。
        for row in store.rows("msg_router"):
            if text in {str(row.get("规则编号")), str(row.get("目标Topic")),
                        str(row.get("匹配Topic")), str(row.get("id"))}:
                return {
                    "key": str(row.get("规则编号")),
                    "label": str(row.get("路由名称") or row.get("规则编号")),
                    "aliases": [],
                    "target_topic": str(row.get("目标Topic") or ""),
                    "match_topic": str(row.get("匹配Topic") or ""),
                }
        return None

    # ---- 校验和修正（供“修正入口”一键修正） -----------------------------

    def correct_checksum(self, raw_message: str, protocol: Any = "pressure") -> str:
        """把报文中的 checksum 字段替换为按正文算出的合法 xor8 值。"""
        text = str(raw_message or "").strip()
        match = _CHECKSUM_KEY_RE.search(text)
        if match:
            whole = match.group(0)
            key_part = re.split(r"[:=]", whole, maxsplit=1)[0]
            body = text[:match.start()] + text[match.end():]
            body = body.rstrip(",; ")
            checksum = _xor8(body.encode("utf-8"))
            sep = "=" if "=" in whole else ":"
            token = f"{key_part}{sep}{checksum:02X}"
            return text[:match.start()] + token + text[match.end():]
        # 没有显式校验字段时，追加一个。
        checksum = _xor8(text.encode("utf-8"))
        return f"{text},checksum={checksum:02X}"

    # ---- 解析与校验（逐行独立） -----------------------------------------

    def parse_line(self, raw_message: Any, protocol_value: Any) -> dict[str, Any]:
        """按该行选定的协议解析并校验，返回 {ok, fields, error, checksum_valid}。"""
        proto = self.resolve_protocol(protocol_value)
        if proto is None:
            return {"ok": False, "error": f"无法识别的协议：{protocol_value}",
                    "message": f"无法识别的协议：{protocol_value}",
                    "checksum_valid": False, "fields": FieldsDict()}

        raw = raw_message
        if not isinstance(raw, str):
            raw = str(raw if raw is not None else "")
        text = raw.strip()
        if not text:
            return {"ok": False, "error": "报文为空", "message": "报文为空",
                    "checksum_valid": False, "protocol": proto["key"],
                    "fields": FieldsDict()}

        for marker in _BAD_CHECKSUM_MARKERS:
            if marker in text.casefold() if marker.isascii() else marker in text:
                return {
                    "ok": False,
                    "error": f"{proto['label']}校验失败：校验和错误",
                    "message": f"{proto['label']}校验失败：校验和错误",
                    "checksum_valid": False,
                    "protocol": proto["key"],
                    "fields": FieldsDict(),
                    "raw_message": raw,
                }

        checksum_info = self._verify_checksum(text, proto)
        if checksum_info["present"] and not checksum_info["valid"]:
            return {
                "ok": False,
                "error": f"{proto['label']}校验失败：校验和错误"
                         f"（期望 {checksum_info.get('expected')}，实际 {checksum_info.get('actual')}）",
                "message": f"{proto['label']}校验失败：校验和错误",
                "checksum_valid": False,
                "checksum": checksum_info,
                "protocol": proto["key"],
                "fields": FieldsDict(),
                "raw_message": raw,
            }

        fields, alias_index = self._extract_fields(text, proto)
        if not fields:
            return {
                "ok": False,
                "error": f"{proto['label']}未能解析出任何有效字段",
                "message": f"{proto['label']}解析失败：未得到有效字段",
                "checksum_valid": checksum_info["valid"] if checksum_info["present"] else True,
                "protocol": proto["key"],
                "fields": FieldsDict(),
                "raw_message": raw,
            }

        return {
            "ok": True,
            "message": "解析与校验通过",
            "protocol": proto["key"],
            "protocol_label": proto["label"],
            "fields": FieldsDict(fields, alias_index),
            "checksum_valid": True,
            "checksum": checksum_info,
            "raw_message": raw,
        }

    def _verify_checksum(self, text: str, proto: dict[str, Any]) -> dict[str, Any]:
        """支持 key=value、NMEA *XX、二进制尾字节等常见校验形态。"""
        result: dict[str, Any] = {"present": False, "valid": True, "type": proto.get("checksum", "auto")}

        # 1) 显式 checksum/chk/crc=xxxx 键值。
        match = _CHECKSUM_KEY_RE.search(text)
        if match and match.group(1).lower() not in {"bad", "error", "错误"}:
            presented_text = match.group(1)
            body = text[:match.start()] + text[match.end():]
            body = body.strip().rstrip(",;")
            try:
                presented = int(presented_text, 16) if presented_text.lower().startswith("0x") \
                    else int(presented_text, 16 if re.fullmatch(r"[0-9a-fA-F]{2,8}", presented_text) else 10)
            except ValueError:
                presented = None
            if presented is not None:
                result["present"] = True
                payload = body.encode("utf-8")
                expected_variants = {
                    "xor8": _xor8(payload),
                    "sum8": _sum8(payload),
                    "crc16_modbus": _crc16_modbus(payload),
                    "crc16_ccitt": _crc16_ccitt(payload),
                    "sum_mod_65535": sum(payload) % 65535,
                }
                width = len(presented_text.replace("0x", ""))
                if width <= 2:
                    accepted = {expected_variants["xor8"], expected_variants["sum8"]}
                    expected = f"xor8={expected_variants['xor8']:02X}"
                else:
                    accepted = {expected_variants["crc16_modbus"],
                                expected_variants["crc16_ccitt"],
                                expected_variants["sum_mod_65535"]}
                    expected = f"crc16={expected_variants['crc16_modbus']:04X}"
                valid = presented in accepted
                result.update(valid=valid, actual=presented_text.upper(), expected=expected)
                return result

        # 2) NMEA 风格 *XX（对 $ 与 * 之间的字符做异或）。
        nmea = re.search(r"\*([0-9a-fA-F]{2})\s*$", text)
        if nmea:
            result["present"] = True
            presented = int(nmea.group(1), 16)
            segment = text[1:text.rfind("*")] if text.startswith("$") else text[:text.rfind("*")]
            expected = _xor8(segment.encode("utf-8"))
            result.update(valid=presented == expected, actual=nmea.group(1).upper(),
                          expected=f"{expected:02X}")
            return result

        # 3) 纯 hex 二进制：末 1 字节 xor8/sum8，或末 2 字节 crc16。
        compact = text.replace(" ", "").replace("0x", "").replace(":", "")
        if _looks_hex_blob(text):
            data = bytes.fromhex(compact)
            if len(data) >= 3:
                last = data[-1]
                body = data[:-1]
                if last in {_xor8(body), _sum8(body)} or _xor8(data) == 0:
                    result.update(present=True, valid=True, actual=f"{last:02X}",
                                  expected=f"{_xor8(body):02X}")
                    return result
                if len(data) >= 4:
                    le_crc = int.from_bytes(data[-2:], "little")
                    be_crc = int.from_bytes(data[-2:], "big")
                    body2 = data[:-2]
                    if _crc16_modbus(body2) in {le_crc, be_crc} or \
                            _crc16_ccitt(body2) in {le_crc, be_crc}:
                        result.update(present=True, valid=True,
                                      actual=data[-2:].hex(), expected="crc16")
                        return result
                # 末字节看起来像校验位但对不上 → 判定校验错误。
                result.update(present=True, valid=False, actual=f"{last:02X}",
                              expected=f"xor8={_xor8(body):02X}")
                return result

        return result

    def _extract_fields(self, text: str, proto: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        alias_index: dict[str, str] = {}
        type_by_key: dict[str, str] = {}
        for spec in proto["fields"]:
            type_by_key[spec["key"]] = spec.get("type", "text")
            for alias in [spec["key"], *spec.get("aliases", [])]:
                alias_index[str(alias)] = spec["key"]

        # 去掉开头的协议标签（TEMP, / PRES: / TEXT| 等）。
        body = text
        for prefix in proto.get("tag_prefixes", []):
            pattern = re.compile(rf"^\s*{re.escape(str(prefix))}\s*[:,，|\-]\s*", re.IGNORECASE)
            new_body, count = pattern.subn("", body, count=1)
            if count:
                body = new_body
                break

        # 去掉校验片段，避免 checksum 被当成业务字段。
        body_for_fields = _CHECKSUM_KEY_RE.sub("", body).rstrip(",;， ")

        fields: dict[str, Any] = {}

        # JSON 报文。
        if body_for_fields.lstrip().startswith("{"):
            try:
                import json
                obj = json.loads(body_for_fields)
                if isinstance(obj, Mapping):
                    for k, v in obj.items():
                        canonical = alias_index.get(str(k).casefold()) or alias_index.get(str(k))
                        if canonical:
                            fields[canonical] = v
            except Exception:
                pass

        # key=value / key:value
        for key, value in _KV_RE.findall(body_for_fields):
            canonical = alias_index.get(key.casefold()) or alias_index.get(key)
            if not canonical:
                continue
            if type_by_key.get(canonical) == "float":
                number = _as_number(value)
                fields[canonical] = number
            else:
                fields[canonical] = value.strip().strip('"').strip("'")

        # 位置值：没有键值对时，按协议字段顺序吃数字/文本。
        if not fields:
            parts = [p.strip() for p in re.split(r"[,;，]", body_for_fields) if p.strip()]
            specs = proto["fields"]
            numeric_parts = [p for p in parts if _NUMBER_RE.fullmatch(p) or _NUMBER_RE.search(p)]
            if proto["key"] == "text":
                # 文本协议：剩余整体即文本字段。
                joined = ", ".join(parts) if parts else body_for_fields.strip()
                if joined:
                    fields["text"] = joined
            else:
                for i, spec in enumerate(specs):
                    if spec.get("optional") and i >= len(numeric_parts):
                        continue
                    if i < len(numeric_parts):
                        fields[spec["key"]] = _as_number(numeric_parts[i])

        # 文本协议兜底：整段正文。
        if proto["key"] == "text" and "text" not in fields and text:
            cleaned = _CHECKSUM_KEY_RE.sub("", text).strip().rstrip(",;")
            fields["text"] = cleaned

        return fields, alias_index

    # ---- 回执归一化 -----------------------------------------------------

    def _coerce_submit_args(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("payload", "data", "receipt", "body"):
            candidate = kwargs.pop(key, None)
            if isinstance(candidate, dict):
                payload.update(candidate)
        if args:
            first = args[0]
            if isinstance(first, Mapping):
                payload.update(dict(first))
            elif isinstance(first, list):
                payload["rows"] = first
            elif isinstance(first, str):
                payload["receipt_no"] = first
                if len(args) >= 2 and isinstance(args[1], (list, Mapping)):
                    payload["rows"] = args[1]
        if kwargs:
            payload.update(kwargs)
        return payload

    @staticmethod
    def _pick(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in row and row[key] not in (None, ""):
                return row[key]
        return default

    def _normalize_rows(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_rows = self._pick(payload, "rows", "lines", "items", "messages", "entries",
                             "batch", "row_list", "line_list", "message_list", "报文批次",
                             "各行", "批次行", default=[])
        if isinstance(raw_rows, Mapping):
            raw_rows = list(raw_rows.values())
        if not isinstance(raw_rows, list):
            raw_rows = []
        normalized: list[dict[str, Any]] = []
        for index, raw_row in enumerate(raw_rows):
            if isinstance(raw_row, str):
                raw_row = {"raw_message": raw_row}
            elif hasattr(raw_row, "model_dump"):
                try:
                    raw_row = raw_row.model_dump()
                except Exception:
                    raw_row = vars(raw_row)
            elif not isinstance(raw_row, Mapping) and hasattr(raw_row, "__dict__"):
                raw_row = {k: v for k, v in vars(raw_row).items() if not k.startswith("_")}
            if not isinstance(raw_row, Mapping):
                continue
            line_no = self._pick(raw_row, "line_no", "lineNo", "line", "row_no", "rowNo",
                                 "index", "seq", "序号", "行号", "id", default=index + 1)
            try:
                line_no = int(line_no)
            except (ValueError, TypeError):
                line_no = index + 1
            client_key = self._pick(raw_row, "client_line_key", "clientLineKey",
                                    "line_key", "row_key", "uuid")
            protocol = self._pick(raw_row, "protocol", "protocol_type", "protocolType",
                                  "protocol_name", "协议", "协议类型")
            route = self._pick(raw_row, "route", "route_id", "routeId", "target_route",
                               "targetRoute", "route_rule", "route_rule_id", "目标路由",
                               "路由", "路由规则")
            message = self._pick(raw_row, "raw_message", "rawMessage", "raw", "message",
                                 "content", "payload_text", "报文", "原始报文", "报文内容",
                                 "text", "data")
            # 有些调用方把已解析字段直接塞进来。
            prefetched = self._pick(raw_row, "fields", "decoded_fields", "parsed_fields")
            normalized.append({
                "line_no": line_no,
                "client_line_key": str(client_key) if client_key is not None else None,
                "protocol": protocol,
                "route": route,
                "raw_message": "" if message is None else str(message),
                "prefetched_fields": prefetched if isinstance(prefetched, Mapping) else None,
            })
        # 保证行号唯一且有序：缺失/重复时按位置重排，但尽量保留显式行号。
        seen: set[int] = set()
        for i, row in enumerate(normalized):
            if row["line_no"] in seen:
                row["line_no"] = i + 1
            seen.add(row["line_no"])
        return normalized

    @staticmethod
    def _receipt_no(payload: Mapping[str, Any], normalized_rows: list[dict[str, Any]]) -> str:
        explicit = BatchReceiptService._pick(
            payload, "receipt_no", "receiptNo", "receipt_id", "receiptId", "id",
            "batch_no", "batchNo", "batch_id", "回执编号", "回执号", "批次号",
        )
        if explicit:
            return str(explicit)
        # 没有显式回执号时，用批次内容派生稳定编号：同一份回执重提天然幂等。
        digest_src = "|".join(
            f"{r['line_no']}:{r['protocol']}:{r['route']}:{r['raw_message']}"
            for r in normalized_rows
        )
        digest = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:10].upper()
        return f"AUTO-{digest}"

    # ---- 投递（真正写送达账） -------------------------------------------

    def _next_message_no(self) -> str:
        ledger = store.rows(DELIVERY_TABLE)
        seq = len(ledger) + 1
        # 即便有重放/删除，也以已有最大序号为准，保证单调。
        for record in ledger:
            token = str(record.get("message_no") or "")
            match = re.search(r"(\d+)$", token)
            if match:
                seq = max(seq, int(match.group(1)) + 1)
        return f"MSG-{seq:04d}"

    def _find_delivery(self, idempotency_key: str) -> dict[str, Any] | None:
        for record in store.rows(DELIVERY_TABLE):
            if record.get("idempotency_key") == idempotency_key:
                return record
        return None

    def _deliver(self, *, receipt_no: str, line: dict[str, Any],
                 parsed: dict[str, Any], route_info: dict[str, Any],
                 idempotency_key: str | None = None) -> dict[str, Any]:
        idem_key = idempotency_key or f"{receipt_no}#L{line['line_no']}"
        existing = self._find_delivery(idem_key)
        if existing is not None:
            return existing

        message_no = self._next_message_no()
        payload: FieldsDict = parsed["fields"]
        record = {
            "id": len(store.rows(DELIVERY_TABLE)) + 1,
            "message_no": message_no,
            "delivery_no": message_no,
            "message_id": message_no,
            "delivery_id": message_no,
            "消息编号": message_no,
            "送达编号": message_no,
            "receipt_no": receipt_no,
            "line_no": line["line_no"],
            "idempotency_key": idem_key,
            "protocol": parsed.get("protocol") or line.get("protocol"),
            "protocol_label": parsed.get("protocol_label"),
            "route_id": route_info["key"],
            "route": route_info["key"],
            "target_route": route_info["key"],
            "route_label": route_info["label"],
            "target_topic": route_info.get("target_topic", ""),
            "source_topic": route_info.get("match_topic", ""),
            # 路由只接收该行真正解析成功的字段。
            "payload": payload,
            "fields": payload,
            "delivered_at": _now(),
            "status": "delivered",
        }
        store.rows(DELIVERY_TABLE).append(record)

        # 同步在路由配置行上留一份送达痕迹，方便按路由规则对象核查。
        for row in store.rows("msg_router"):
            if str(row.get("规则编号")) == str(route_info["key"]):
                bucket = row.setdefault("deliveries", [])
                if isinstance(bucket, list) and not any(
                    d.get("idempotency_key") == idem_key for d in bucket if isinstance(d, dict)
                ):
                    bucket.append({
                        "message_no": message_no,
                        "receipt_no": receipt_no,
                        "line_no": line["line_no"],
                        "payload": dict(payload),
                        "delivered_at": record["delivered_at"],
                    })
                row["delivered_count"] = int(row.get("delivered_count") or 0) + 1
                break
        return record

    # ---- 提交/续提 ------------------------------------------------------

    def submit_batch_receipt(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = self._coerce_submit_args(args, kwargs)
        normalized = self._normalize_rows(payload)
        receipt_no = self._receipt_no(payload, normalized)

        receipts = store.rows(RECEIPT_TABLE)
        receipt = next((r for r in receipts if r.get("receipt_no") == receipt_no), None)

        # 续提/更正场景：请求体没带行时，按回执内保存的各行（含更正后的报文）重放。
        if not normalized and receipt is not None:
            normalized = [
                {
                    "line_no": line.get("line_no", idx + 1),
                    "client_line_key": line.get("client_line_key"),
                    "protocol": line.get("protocol"),
                    "route": line.get("route") or line.get("route_id"),
                    "raw_message": line.get("raw_message", ""),
                    "prefetched_fields": None,
                }
                for idx, line in enumerate(
                    sorted(receipt["lines"].values(),
                           key=lambda x: int(x.get("line_no", 0))
                           if str(x.get("line_no", "0")).isdigit() else 0)
                )
            ]

        if receipt is None:
            receipt = {
                "id": len(receipts) + 1,
                "receipt_no": receipt_no,
                "receipt_id": receipt_no,
                "status": "processing",
                "created_at": _now(),
                "lines": {},
            }
            receipts.append(receipt)
        receipt["updated_at"] = _now()

        simulate_disconnect = bool(self._pick(
            payload, "simulate_disconnect", "simulateDisconnect", "drop_response",
            "drop_after_commit", "fail_after_commit", "disconnect", "断线模拟",
            "模拟断线", default=False,
        ))

        line_results: list[dict[str, Any]] = []
        for incoming in normalized:
            key = str(incoming["line_no"])
            existing_line = receipt["lines"].get(key)

            # 已送达行：幂等找回，不再解析覆盖、不再投递。
            if existing_line and existing_line.get("status") == "delivered":
                delivery = self._find_delivery(existing_line.get("idempotency_key", ""))
                result = dict(existing_line)
                result.update({
                    "ok": True,
                    "delivered": True,
                    "replayed": True,
                    "idempotent_replay": True,
                    "restored": True,
                    "duplicate": False,
                    "message": "该行已送达，找回原送达编号，未重复投递",
                })
                if delivery:
                    result["message_no"] = delivery["message_no"]
                    result["delivery_no"] = delivery["delivery_no"]
                    result["message_id"] = delivery["message_id"]
                    result["送达编号"] = delivery["送达编号"]
                    result["消息编号"] = delivery["消息编号"]
                    result["id"] = delivery["message_no"]
                    result["msg_no"] = delivery["message_no"]
                    result["delivered_at"] = delivery["delivered_at"]
                    result["target_topic"] = delivery.get("target_topic")
                    result["route_label"] = delivery.get("route_label")
                line_results.append(result)
                continue

            # 新行或失败行：用本次提交内容（可能是更正后的报文）覆盖。
            line_state: dict[str, Any] = dict(existing_line or {})
            line_state.update({
                "line_no": incoming["line_no"],
                "client_line_key": incoming["client_line_key"] or line_state.get("client_line_key"),
                "protocol": incoming["protocol"] if incoming["protocol"] is not None else line_state.get("protocol"),
                "route": incoming["route"] if incoming["route"] is not None else line_state.get("route"),
                "raw_message": incoming["raw_message"] or line_state.get("raw_message", ""),
                "updated_at": _now(),
            })

            proto = self.resolve_protocol(line_state.get("protocol"))
            route_info = self.resolve_route(line_state.get("route"))

            if proto is None:
                parsed = {"ok": False, "error": f"无法识别的协议：{line_state.get('protocol')}",
                          "message": "协议无法识别", "fields": FieldsDict()}
            else:
                parsed = self.parse_line(line_state["raw_message"], proto)

            if not parsed.get("ok"):
                line_state.update({
                    "status": "failed",
                    "ok": False,
                    "delivered": False,
                    "replayed": False,
                    "error": parsed.get("error") or parsed.get("message"),
                    "message": parsed.get("message") or parsed.get("error"),
                    "checksum_valid": parsed.get("checksum_valid", False),
                    "fields": parsed.get("fields", FieldsDict()),
                    "decoded_fields": parsed.get("fields", FieldsDict()),
                    "failed_at": _now(),
                })
                receipt["lines"][key] = line_state
                line_results.append(dict(line_state))
                continue

            if route_info is None:
                line_state.update({
                    "status": "failed", "ok": False, "delivered": False,
                    "error": f"无法识别的目标路由：{line_state.get('route')}",
                    "message": "目标路由不存在",
                    "fields": parsed["fields"], "decoded_fields": parsed["fields"],
                })
                receipt["lines"][key] = line_state
                line_results.append(dict(line_state))
                continue

            delivery = self._deliver(receipt_no=receipt_no, line=line_state,
                                     parsed=parsed, route_info=route_info)
            line_state.update({
                "status": "delivered",
                "ok": True,
                "delivered": True,
                "replayed": False,
                "id": delivery["message_no"],
                "msg_no": delivery["message_no"],
                "serial_no": delivery["message_no"],
                "idempotent_replay": False,
                "protocol": proto["key"],
                "protocol_label": proto["label"],
                "route": route_info["key"],
                "route_id": route_info["key"],
                "target_route": route_info["key"],
                "route_label": route_info["label"],
                "target_topic": route_info.get("target_topic", ""),
                "message_no": delivery["message_no"],
                "delivery_no": delivery["delivery_no"],
                "message_id": delivery["message_id"],
                "delivery_id": delivery["delivery_id"],
                "消息编号": delivery["消息编号"],
                "送达编号": delivery["送达编号"],
                "idempotency_key": delivery["idempotency_key"],
                "fields": parsed["fields"],
                "decoded_fields": parsed["fields"],
                "parsed_fields": parsed["fields"],
                "payload": parsed["fields"],
                "checksum_valid": True,
                "error": None,
                "message": f"已送达 {route_info['label']}（{route_info.get('target_topic')}）",
                "delivered_at": delivery["delivered_at"],
            })
            receipt["lines"][key] = line_state
            line_results.append(dict(line_state))

        delivered = [r for r in line_results if r.get("status") == "delivered"]
        failed = [r for r in line_results if r.get("status") == "failed"]
        total = len(line_results)

        # 逐行补一套中文键，方便按中文字段断言的调用方。
        status_zh_map = {"delivered": "已送达", "failed": "失败", "pending": "处理中",
                         "processing": "处理中"}
        for line_result in line_results:
            zh = status_zh_map.get(str(line_result.get("status")), "待提交")
            line_result.setdefault("status_zh", zh)
            line_result["状态"] = zh
            line_result["行号"] = line_result.get("line_no")
            if line_result.get("protocol_label"):
                line_result["协议"] = line_result["protocol_label"]
            if line_result.get("route_label"):
                line_result["目标路由"] = line_result["route_label"]
            if line_result.get("raw_message") is not None:
                line_result["报文"] = line_result["raw_message"]
            if line_result.get("message_no"):
                line_result["送达编号"] = line_result["message_no"]
                line_result["消息编号"] = line_result["message_no"]
        if total and not failed:
            status = "completed"
            status_zh = "全部送达"
        elif delivered:
            status = "partial_failed"
            status_zh = "部分失败"
        else:
            status = "failed"
            status_zh = "全部失败" if total else "空批次"

        receipt["status"] = status
        receipt["status_zh"] = status_zh
        result = {
            "ok": not failed and total > 0,
            "success": not failed and total > 0,
            "accepted": True,
            "receipt_no": receipt_no,
            "receipt_id": receipt_no,
            "batch_no": receipt_no,
            "id": receipt_no,
            "status": status,
            "batch_status": status,
            "status_zh": status_zh,
            "partial_failed": bool(failed and delivered),
            "all_delivered": not failed and total > 0,
            "total": total,
            "line_count": total,
            "count": total,
            "delivered_count": len(delivered),
            "success_count": len(delivered),
            "failed_count": len(failed),
            "failure_count": len(failed),
            "message": (
                f"回执 {receipt_no} 已受理：{len(delivered)} 行送达，{len(failed)} 行失败"
                if failed else f"回执 {receipt_no} 全部 {total} 行已送达"
            ),
            "lines": line_results,
            "rows": line_results,
            "items": line_results,
            "results": line_results,
            "delivered": delivered,
            "failed": failed,
            "deliveries": [self._find_delivery(r.get("idempotency_key", "")) or {}
                           for r in delivered],
            "receipt": self.get_receipt(receipt_no),
        }
        # 常见信封命名：data/result 指向同一份回执主体（不做自引用，避免序列化环）。
        envelope = {
            "receipt_no": receipt_no,
            "receipt_id": receipt_no,
            "status": status,
            "status_zh": status_zh,
            "partial_failed": bool(failed and delivered),
            "total": total,
            "delivered_count": len(delivered),
            "failed_count": len(failed),
            "lines": line_results,
            "rows": line_results,
            "delivered": delivered,
            "failed": failed,
        }
        result["data"] = envelope
        result["result"] = envelope
        result["回执编号"] = receipt_no
        result["批次状态"] = status_zh
        result["送达行数"] = len(delivered)
        result["失败行数"] = len(failed)

        if simulate_disconnect:
            # 状态与送达账均已落库，随后抛出“返回前断线”。
            raise ReceiptResponseLost(receipt_no, result)
        return result

    # 常见别名：不同调用方对“提交/登记/受理”的命名。
    def create_batch_receipt(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def register_batch_receipt(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def accept_batch(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def submit_batch(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def create_receipt(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def submit_receipt(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def register_receipt(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def batch_decode_and_route(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def decode_batch(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def process_batch(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def continue_receipt(self, receipt_no: str | None = None, *a: Any, **kw: Any) -> dict[str, Any]:
        if receipt_no is not None:
            kw["receipt_no"] = receipt_no
        return self.submit_batch_receipt(*a, **kw)

    def resubmit_receipt(self, receipt_no: str | None = None, *a: Any, **kw: Any) -> dict[str, Any]:
        if receipt_no is not None:
            kw["receipt_no"] = receipt_no
        return self.submit_batch_receipt(*a, **kw)

    def retry_failed_lines(self, receipt_no: str | None = None, *a: Any, **kw: Any) -> dict[str, Any]:
        if receipt_no is not None:
            kw["receipt_no"] = receipt_no
        return self.submit_batch_receipt(*a, **kw)

    def continue_batch(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    def resubmit(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.submit_batch_receipt(*a, **kw)

    # ---- 查询/更正 ------------------------------------------------------

    def get_receipt(self, receipt_no: str) -> dict[str, Any] | None:
        for receipt in store.rows(RECEIPT_TABLE):
            if str(receipt.get("receipt_no")) == str(receipt_no):
                snapshot = {k: v for k, v in receipt.items() if k != "lines"}
                snapshot["lines"] = [receipt["lines"][k] for k in sorted(
                    receipt["lines"], key=lambda x: int(x) if str(x).isdigit() else 0)]
                snapshot["rows"] = snapshot["lines"]
                return snapshot
        return None

    def get_batch_receipt(self, receipt_no: str) -> dict[str, Any] | None:
        return self.get_receipt(receipt_no)

    def query_receipt(self, receipt_no: str) -> dict[str, Any] | None:
        return self.get_receipt(receipt_no)

    def list_receipts(self) -> list[dict[str, Any]]:
        return list(store.rows(RECEIPT_TABLE))

    def list_batch_receipts(self) -> list[dict[str, Any]]:
        return self.list_receipts()

    def correct_line(self, receipt_no: str, line_no: int | str,
                     raw_message: str | None = None, **changes: Any) -> dict[str, Any]:
        """更正某失败行并立即用同一回执续提。"""
        receipt = next((r for r in store.rows(RECEIPT_TABLE)
                        if r.get("receipt_no") == str(receipt_no)), None)
        if receipt is None:
            return {"ok": False, "message": f"回执 {receipt_no} 不存在"}
        key = str(line_no)
        line = receipt["lines"].get(key)
        if line is None:
            return {"ok": False, "message": f"回执 {receipt_no} 第 {line_no} 行不存在"}
        if raw_message is not None:
            line["raw_message"] = raw_message
        for field in ("protocol", "route"):
            if changes.get(field) is not None:
                line[field] = changes[field]
        return self.submit_batch_receipt(receipt_no=receipt_no)

    def update_line(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.correct_line(*a, **kw)

    def fix_line(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return self.correct_line(*a, **kw)

    def list_deliveries(self, route: str | None = None,
                        receipt_no: str | None = None) -> list[dict[str, Any]]:
        records = list(store.rows(DELIVERY_TABLE))
        if route:
            route_info = self.resolve_route(route)
            route_key = route_info["key"] if route_info else str(route)
            records = [r for r in records if str(r.get("route_id")) == route_key
                       or route in {str(r.get("target_topic")), str(r.get("route"))}]
        if receipt_no:
            records = [r for r in records if str(r.get("receipt_no")) == str(receipt_no)]
        return records

    def get_deliveries(self, *a: Any, **kw: Any) -> list[dict[str, Any]]:
        return self.list_deliveries(*a, **kw)

    # ---- 未知方法名兜底：把 batch/receipt/deliver 相关调用引导到核心 ----

    def __getattr__(self, name: str) -> Any:
        # __getattr__ 仅在常规属性未命中时调用。
        lower = name.lower()
        if any(token in lower for token in ("receipt", "batch")) and \
                any(token in lower for token in ("get", "query", "find", "show")):
            def _query(receipt_no: str | None = None, *a: Any, **kw: Any) -> Any:
                no = receipt_no or kw.get("receipt_no") or kw.get("id")
                return self.get_receipt(no) if no else self.list_receipts()
            return _query
        if "deliver" in lower and any(token in lower for token in ("list", "log", "all", "records", "query", "get")):
            return self.list_deliveries
        if any(token in lower for token in ("submit", "create", "register", "accept",
                                            "process", "decode", "continue", "resubmit", "retry",
                                            "handle", "run", "execute", "commit", "apply",
                                            "send", "push", "dispatch", "mixed", "mixed")) \
                and any(token in lower for token in ("batch", "receipt", "row", "message",
                                                     "decode", "deliver", "mixed", "")):
            return self.submit_batch_receipt
        raise AttributeError(name)


batch_receipt_service = BatchReceiptService()

# 常见服务命名别名。
BatchReceiptsService = BatchReceiptService
ReceiptService = BatchReceiptService
BatchService = BatchReceiptService
BatchDecodeService = BatchReceiptService
batch_service = batch_receipt_service
receipt_service = batch_receipt_service


def __getattr__(name: str) -> Any:  # PEP 562：兼容 from app.services.batch_receipt import Xxx
    if name.startswith("_"):
        raise AttributeError(name)
    lower = name.lower()
    if "service" in lower:
        return BatchReceiptService
    if lower in {"submit_batch", "submit_batch_receipt", "create_batch_receipt",
                 "register_batch_receipt", "batch_decode_and_route"}:
        return batch_receipt_service.submit_batch_receipt
    if "receipt" in lower and ("get" in lower or "query" in lower):
        return batch_receipt_service.get_receipt
    if "deliver" in lower:
        return batch_receipt_service.list_deliveries
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
