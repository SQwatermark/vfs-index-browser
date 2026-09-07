"""枚举名称导出的唯一查询入口；目录由 metadata 批量生成，不维护游戏枚举表。"""

from __future__ import annotations


class NativeEnumCatalog:
    def __init__(self, payload: dict | None):
        self.types: dict[str, list[dict]] = {}
        if payload is None:
            return
        if payload.get("format") != "VfsNativeEnumCatalog" or payload.get("version") != 1:
            raise ValueError("unsupported native enum catalog")
        for item in payload["enums"]:
            self.types.setdefault(item["type"].replace("+", "."), []).append(item)

    def get(self, type_name: str) -> dict | None:
        items = self.types.get(type_name.replace("+", "."), [])
        if len(items) > 1:
            raise ValueError(f"ambiguous enum assembly for {type_name}")
        return items[0] if items else None

    def name(self, type_name: str, value: int) -> str:
        item = self.get(type_name)
        if item is None:
            raise ValueError(f"missing enum metadata for {type_name}")
        names = [member["name"] for member in item["members"] if member["value"] == value]
        if not names and item.get("isFlags") is True and value > 0:
            remaining = value
            names = []
            for member in item["members"]:
                bit = member["value"]
                if bit > 0 and bit & (bit - 1) == 0 and remaining & bit:
                    names.append(member["name"])
                    remaining &= ~bit
            if remaining:
                names = []
        if not names:
            # 不把未知整数或 flags 未知位伪装成合法名字。
            raise ValueError(f"unknown enum value {type_name}: {value}")
        # 同值别名保留于目录；导出稳定选择 metadata 声明顺序中的第一个名字。
        return ", ".join(names) if item.get("isFlags") is True else names[0]
