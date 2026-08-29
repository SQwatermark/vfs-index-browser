"""Application service for the indexed Wwise virtual catalog."""

from __future__ import annotations

from contextlib import closing
from typing import Callable, Mapping
from urllib.parse import unquote

from wwise_store import (
    get_wwise_bank,
    get_wwise_event,
    get_wwise_media,
    list_wwise_banks,
    list_wwise_events,
    list_wwise_media,
    list_wwise_media_prefixes,
    wwise_summary,
)


class WwiseCatalogService:
    def __init__(self, connect: Callable, *, page_size_max: int) -> None:
        self._connect = connect
        self._page_size_max = page_size_max

    def list(self, query: Mapping[str, list[str]]) -> dict:
        path = unquote(query.get("path", [""])[0]).replace("\\", "/").strip("/")
        page = max(int(query.get("page", ["1"])[0]), 1)
        page_size = min(
            max(int(query.get("pageSize", ["100"])[0]), 1),
            self._page_size_max,
        )
        offset = (page - 1) * page_size
        with closing(self._connect()) as conn:
            summary = wwise_summary(conn)
            dirs: list[dict] = []
            files: list[dict] = []
            total = 0
            if not path:
                dirs = [
                    self._directory("Events", summary["eventCount"]),
                    self._directory("Banks", summary["bankCount"]),
                    self._directory(
                        "Media", summary["mediaCount"], summary["mediaBytes"]
                    ),
                ]
            elif path == "Events":
                total, rows = list_wwise_events(
                    conn, limit=page_size, offset=offset
                )
                files = [self.event_file(row) for row in rows]
            elif path == "Banks":
                total, rows = list_wwise_banks(
                    conn, limit=page_size, offset=offset
                )
                files = [self.bank_file(row) for row in rows]
            elif path == "Media":
                dirs = [
                    {
                        "name": row["prefix"],
                        "path": f"Media/{row['prefix']}",
                        "file_count": row["file_count"],
                        "total_bytes": row["total_bytes"],
                    }
                    for row in list_wwise_media_prefixes(conn)
                ]
            elif path.startswith("Media/") and path.count("/") == 1:
                prefix = path.split("/", 1)[1].casefold()
                total, rows = list_wwise_media(
                    conn, prefix, limit=page_size, offset=offset
                )
                files = [self.media_file(row) for row in rows]
            else:
                raise FileNotFoundError("Wwise virtual directory not found")

        return {
            "path": path,
            "summary": summary,
            "directory": {
                "path": path,
                "file_count": total if path else sum(item["file_count"] for item in dirs),
                "total_bytes": sum(item["total_bytes"] for item in dirs),
                "encrypted_count": 0,
                "missing_chunk_count": 0,
            },
            "dirs": dirs,
            "files": files,
            "page": {
                "page": page,
                "pageSize": page_size,
                "total": total,
                "pages": max((total + page_size - 1) // page_size, 1),
            },
        }

    def preview(self, query: Mapping[str, list[str]]) -> dict:
        kind = query.get("kind", [""])[0]
        pck_file_id = int(query.get("pckFileId", [""])[0])
        with closing(self._connect()) as conn:
            if kind == "event":
                bank_id = int(query.get("bankId", [""])[0])
                event_id = int(query.get("eventId", [""])[0])
                event = get_wwise_event(conn, pck_file_id, bank_id, event_id)
                if event is None:
                    raise FileNotFoundError("Wwise event not found")
                for media in event["media"]:
                    media["rawUrl"] = self.media_raw_url(media, "wav")
                    media["wemDownloadUrl"] = self.media_raw_url(
                        media, "wem", download=True
                    )
                return {"kind": "wwiseEvent", "event": event}
            if kind == "bank":
                bank_id = int(query.get("bankId", [""])[0])
                bank = get_wwise_bank(conn, pck_file_id, bank_id)
                if bank is None:
                    raise FileNotFoundError("Wwise bank not found")
                return {"kind": "wwiseBank", "bank": bank}
            if kind == "media":
                ordinal = int(query.get("ordinal", [""])[0])
                media = get_wwise_media(conn, pck_file_id, ordinal)
                if media is None:
                    raise FileNotFoundError("Wwise media not found")
                return {
                    "kind": "wwiseMedia",
                    "media": media,
                    "rawUrl": self.media_raw_url(media, "wav"),
                    "wemDownloadUrl": self.media_raw_url(media, "wem", download=True),
                    "wavDownloadUrl": self.media_raw_url(media, "wav", download=True),
                }
        raise ValueError("Wwise preview kind must be event, bank or media")

    @staticmethod
    def _directory(name: str, file_count: int, total_bytes: int = 0) -> dict:
        return {
            "name": name,
            "path": name,
            "file_count": file_count,
            "total_bytes": total_bytes,
        }

    @staticmethod
    def event_file(row: dict) -> dict:
        params = (
            f"kind=event&pckFileId={row['pck_file_id']}&bankId={row['bank_id']}"
            f"&eventId={row['event_id']}"
        )
        return {
            "name": f"{row['event_id']}.event",
            "path": f"Events/{row['event_id']}.event",
            "file_name": row["logical_path"],
            "source": "Wwise Event",
            "block_name": str(row["bank_id"]),
            "chunk_file": f"{row['direct_relation_count']} direct relations",
            "offset": row["payload_offset"],
            "length": row["payload_size"],
            "encrypted": False,
            "chunk_exists": True,
            "virtualKind": "wwiseEvent",
            "previewUrl": f"/api/wwise/preview?{params}",
        }

    @staticmethod
    def bank_file(row: dict) -> dict:
        params = f"kind=bank&pckFileId={row['pck_file_id']}&bankId={row['bank_id']}"
        return {
            "name": f"{row['bank_id']}.bnk",
            "path": f"Banks/{row['bank_id']}.bnk",
            "file_name": row["logical_path"],
            "source": "Wwise Bank",
            "block_name": str(row["pck_file_id"]),
            "chunk_file": (
                f"{row['object_count']} objects / {row['relation_count']} relations"
                f" / {row['diagnostic_count']} diagnostics"
            ),
            "offset": row["offset"],
            "length": row["size"],
            "encrypted": bool(row["encrypted"]),
            "chunk_exists": True,
            "virtualKind": "wwiseBank",
            "previewUrl": f"/api/wwise/preview?{params}",
        }

    @staticmethod
    def media_file(row: dict) -> dict:
        params = f"kind=media&pckFileId={row['pck_file_id']}&ordinal={row['ordinal']}"
        return {
            "name": f"{row['media_id']}.wem",
            "path": f"Media/{row['media_id'][-2:]}/{row['media_id']}.wem",
            "file_name": row["logical_path"],
            "source": row["source"],
            "block_name": row["language"] or "sfx",
            "chunk_file": str(row["pck_file_id"]),
            "offset": row["offset"],
            "length": row["size"],
            "encrypted": bool(row["bank_encrypted"]),
            "chunk_exists": True,
            "virtualKind": "wwiseMedia",
            "previewUrl": f"/api/wwise/preview?{params}",
        }

    @staticmethod
    def media_raw_url(media: dict, mode: str, *, download: bool = False) -> str:
        url = (
            f"/api/wwise/raw?pckFileId={media['pck_file_id']}"
            f"&ordinal={media['ordinal']}&format={mode}"
        )
        return f"{url}&download=1" if download else url
