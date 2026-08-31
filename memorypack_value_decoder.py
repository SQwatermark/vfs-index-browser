"""通用 MemoryPack 文件解码适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class MemoryPackValueDecodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class DecodedMemoryPackValue:
    class_name: str
    value: object
    byte_count: int
    consumed: int
    discovered_unions: dict

    @property
    def complete(self) -> bool:
        return self.consumed == self.byte_count


class MemoryPackValueDecoder:
    """隔离可选解码器加载，并保留完整消费与 union 诊断。"""

    def __init__(
        self,
        class_inference: Callable[[str | None], str | None],
        inputs_provider: Callable[[], tuple[object, dict]],
        file_reader: Callable[[dict, Path], bytes],
        reader_type: object,
        decoder_type: object,
        decode_error_type: type[Exception] | None,
    ) -> None:
        self._infer_class = class_inference
        self._inputs = inputs_provider
        self._read_file = file_reader
        self._reader_type = reader_type
        self._decoder_type = decoder_type
        self._decode_error_type = decode_error_type

    def decode(
        self,
        logical_id: str | None,
        record: dict,
        chunk_path: Path,
    ) -> DecodedMemoryPackValue | None:
        class_name = self._infer_class(logical_id)
        if not class_name:
            return None
        try:
            schema, union_map = self._inputs()
            data = self._read_file(record, chunk_path)
            reader = self._reader_type(data)
            decoder = self._decoder_type(schema, union_map=union_map)
            value = decoder.decode(reader, class_name)
        except (RuntimeError, ValueError) as error:
            raise MemoryPackValueDecodeError(str(error)) from error
        except Exception as error:
            if self._decode_error_type is None or not isinstance(
                error, self._decode_error_type
            ):
                raise
            message = str(error)
            if all(hasattr(error, name) for name in ("message", "offset", "path")):
                message = (
                    f"{error.message} at 0x{error.offset:x} ({error.path})"
                )
            raise MemoryPackValueDecodeError(message) from error
        return DecodedMemoryPackValue(
            class_name=class_name,
            value=value,
            byte_count=len(data),
            consumed=reader.tell(),
            discovered_unions=getattr(decoder, "discovered_unions", {}),
        )
