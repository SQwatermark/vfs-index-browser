from __future__ import annotations

import math
import struct


def canonical_float32(value: float) -> float:
    """返回能重新编码为同一 binary32 的最短十进制值。

    Python 会把 ``struct.unpack('<f', ...)`` 的结果扩宽成 binary64。直接写入
    JSON 时，0.05f 因而会变成 0.05000000074505806。这里最多尝试 binary32
    所需的 9 位有效数字，只缩短展示形式，不改变重新编码后的 32 位内容。
    非有限值仍交给既有调用边界处理。
    """

    if not math.isfinite(value) or value == 0:
        return value
    encoded = struct.pack("<f", value)
    for digits in range(1, 10):
        candidate = float(format(value, f".{digits}g"))
        # 最大有限值的低精度候选可能向外舍入至溢出；继续增加位数而不是中止解码。
        try:
            candidate_encoded = struct.pack("<f", candidate)
        except OverflowError:
            continue
        if candidate_encoded == encoded:
            return candidate
    return value
