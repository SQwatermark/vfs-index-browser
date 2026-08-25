"""Parse and query Endfield ``manifest.hgmmap`` as a logical file tree."""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import brotli


HEAD1 = 0xFF11FF11
HEAD2 = 0xF1F2F3F4
BUNDLE_RECORD_SIZE = 48
ASSET_RECORD_SIZE = 24
SCHEMA_VERSION = 3


def _dotnet_string(data: bytes, offset: int) -> tuple[str, int]:
    length = struct.unpack_from("<I", data, offset)[0]
    start = offset + 4
    end = start + length * 2
    if end > len(data):
        raise ValueError(f"UTF-16 string exceeds manifest at 0x{offset:x}")
    return data[start:end].decode("utf-16-le"), end


def _header(data: bytes) -> tuple[dict, int]:
    if struct.unpack_from("<I", data)[0] != HEAD1:
        raise ValueError("invalid HGM manifest header")
    version, offset = _dotnet_string(data, 4)
    if struct.unpack_from("<I", data, offset)[0] != HEAD2:
        raise ValueError("invalid HGM manifest body header")
    manifest_hash, offset = _dotnet_string(data, offset + 4)
    perforce_cl, offset = _dotnet_string(data, offset)
    return {"version": version, "hash": manifest_hash, "perforceCL": perforce_cl}, offset


def _layout(data: bytes, body_offset: int) -> tuple[int, int, int, int, int]:
    first_value_offset, asset_count = struct.unpack_from("<II", data, body_offset)
    assets_offset = body_offset + 8 + asset_count * 8
    bundle_table = body_offset + first_value_offset + 4
    bundle_value_offset, bundle_count = struct.unpack_from("<II", data, bundle_table)
    bundle_values = bundle_table + bundle_value_offset
    if struct.unpack_from("<I", data, bundle_values + 8)[0] != bundle_count:
        raise ValueError("bundle count mismatch")
    bundles_offset = bundle_values + 12
    data_offset = bundles_offset + bundle_count * BUNDLE_RECORD_SIZE + 4
    return assets_offset, asset_count, bundles_offset, bundle_count, data_offset


def _ref_string(data: bytes, base: int, ref: int) -> str:
    offset = base + ref
    byte_count = struct.unpack_from("<i", data, offset)[0]
    if byte_count < 0 or offset + 4 + byte_count > len(data):
        raise ValueError(f"invalid manifest string at 0x{offset:x}")
    return data[offset + 4 : offset + 4 + byte_count].decode("utf-16-le")


def _compressed_string(data: bytes, base: int, ref: int) -> str:
    offset = base + ref
    byte_count = struct.unpack_from("<I", data, offset)[0]
    if offset + 4 + byte_count > len(data):
        raise ValueError(f"invalid compressed string at 0x{offset:x}")
    return brotli.decompress(data[offset + 4 : offset + 4 + byte_count]).decode("utf-16-le")


def _ref_int_array(data: bytes, base: int, ref: int, limit: int) -> list[int]:
    offset = base + ref
    count = struct.unpack_from("<i", data, offset)[0]
    end = offset + 4 + count * 4
    if count < 0 or end > len(data):
        raise ValueError(f"invalid manifest integer array at 0x{offset:x}")
    values = list(struct.unpack_from(f"<{count}i", data, offset + 4)) if count else []
    if any(value < 0 or value >= limit for value in values):
        raise ValueError(f"manifest bundle dependency is out of range at 0x{offset:x}")
    return values


def _normal_path(raw: str) -> str:
    path = raw.replace("\\", "/").strip("/")
    parts = [part for part in path.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"invalid logical asset path: {raw!r}")
    return "/".join(parts)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ManifestIndex:
    """Persistent, read-optimized view of one decompressed HGM manifest."""

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path

    @classmethod
    def ensure(cls, compressed_payload: bytes, cache_dir: Path) -> "ManifestIndex":
        fingerprint = hashlib.sha256(compressed_payload).hexdigest()
        cache_path = cache_dir / f"manifest-{fingerprint}.sqlite"
        index = cls(cache_path)
        if index._valid(fingerprint):
            return index
        data = brotli.decompress(compressed_payload)
        index._build(data, fingerprint)
        return index

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.cache_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _valid(self, fingerprint: str) -> bool:
        if not self.cache_path.exists():
            return False
        try:
            with self._connect() as conn:
                meta = dict(conn.execute("SELECT key, value FROM meta"))
            return meta.get("schemaVersion") == str(SCHEMA_VERSION) and meta.get("fingerprint") == fingerprint
        except sqlite3.Error:
            return False

    def _build(self, data: bytes, fingerprint: str) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        header, body_offset = _header(data)
        assets_offset, asset_count, bundles_offset, bundle_count, data_offset = _layout(data, body_offset)
        conn = sqlite3.connect(temporary)
        try:
            conn.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE bundle_dependencies (
                    bundle_index INTEGER NOT NULL, dependency_index INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (bundle_index, dependency_index, kind)
                );
                CREATE TABLE assets (
                    asset_index INTEGER PRIMARY KEY, path TEXT NOT NULL, parent TEXT NOT NULL,
                    name TEXT NOT NULL, bundle_index INTEGER NOT NULL, size INTEGER NOT NULL,
                    path_hash TEXT NOT NULL
                );
                CREATE TABLE directories (
                    path TEXT PRIMARY KEY, parent TEXT NOT NULL, name TEXT NOT NULL,
                    file_count INTEGER NOT NULL, total_bytes INTEGER NOT NULL
                );
                CREATE INDEX assets_parent_name ON assets(parent, name COLLATE NOCASE);
                CREATE INDEX assets_path ON assets(path);
                CREATE INDEX assets_path_nocase ON assets(path COLLATE NOCASE);
                CREATE INDEX dirs_parent_name ON directories(parent, name COLLATE NOCASE);
                CREATE INDEX dependencies_bundle_kind ON bundle_dependencies(bundle_index, kind);
            """)
            bundle_rows = []
            dependency_rows = []
            for index in range(bundle_count):
                record = struct.unpack_from("<6I4I2I", data, bundles_offset + index * BUNDLE_RECORD_SIZE)
                if record[0] != index:
                    raise ValueError(f"bundle index mismatch at {index}")
                bundle_rows.append((index, _ref_string(data, data_offset, record[1])))
                for kind, reference in (
                    ("all", record[2]),
                    ("reverseDirect", record[3]),
                    ("direct", record[4]),
                ):
                    dependency_rows.extend(
                        (index, dependency, kind)
                        for dependency in _ref_int_array(data, data_offset, reference, bundle_count)
                    )
                if len(dependency_rows) >= 100_000:
                    conn.executemany("INSERT INTO bundle_dependencies VALUES (?, ?, ?)", dependency_rows)
                    dependency_rows.clear()
            conn.executemany("INSERT INTO bundles VALUES (?, ?)", bundle_rows)
            if dependency_rows:
                conn.executemany("INSERT INTO bundle_dependencies VALUES (?, ?, ?)", dependency_rows)

            directories: dict[str, list[int | str]] = {"": ["", "", 0, 0]}
            batch = []
            for index in range(asset_count):
                path_hash, path_ref, bundle_index, size, padding = struct.unpack_from(
                    "<qIiiI", data, assets_offset + index * ASSET_RECORD_SIZE
                )
                if padding or not 0 <= bundle_index < bundle_count or size < 0:
                    raise ValueError(f"invalid asset record at {index}")
                path = _normal_path(_compressed_string(data, data_offset, path_ref))
                pure = PurePosixPath(path)
                parent = "" if str(pure.parent) == "." else pure.parent.as_posix()
                batch.append((index, path, parent, pure.name, bundle_index, size, f"{path_hash & 0xffffffffffffffff:016x}"))
                directories[""][2] += 1
                directories[""][3] += size
                current = ""
                for part in pure.parts[:-1]:
                    next_path = f"{current}/{part}".strip("/")
                    entry = directories.setdefault(next_path, [current, part, 0, 0])
                    entry[2] += 1
                    entry[3] += size
                    current = next_path
                if len(batch) >= 5000:
                    conn.executemany("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
            if batch:
                conn.executemany("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
            conn.executemany(
                "INSERT INTO directories VALUES (?, ?, ?, ?, ?)",
                [(path, *values) for path, values in directories.items()],
            )
            meta = {
                "schemaVersion": str(SCHEMA_VERSION), "fingerprint": fingerprint,
                "version": header["version"], "hash": header["hash"],
                "perforceCL": header["perforceCL"], "bundleCount": str(bundle_count),
                "assetCount": str(asset_count),
            }
            conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
            conn.commit()
        finally:
            conn.close()
        self.cache_path.unlink(missing_ok=True)
        temporary.replace(self.cache_path)

    def list(self, path: str, page: int = 1, page_size: int = 100) -> dict:
        path = path.replace("\\", "/").strip("/")
        offset = (page - 1) * page_size
        with self._connect() as conn:
            current = conn.execute("SELECT * FROM directories WHERE path = ?", (path,)).fetchone()
            if current is None:
                raise FileNotFoundError(path)
            dirs = [dict(row) for row in conn.execute(
                "SELECT path, name, file_count AS fileCount, total_bytes AS totalBytes FROM directories WHERE parent = ? AND path != ? ORDER BY name COLLATE NOCASE",
                (path, path),
            )]
            total = conn.execute("SELECT COUNT(*) FROM assets WHERE parent = ?", (path,)).fetchone()[0]
            files = [dict(row) for row in conn.execute("""
                SELECT a.asset_index AS assetIndex, a.path, a.name, a.size, a.path_hash AS pathHash,
                       a.bundle_index AS bundleIndex, b.name AS bundleName
                FROM assets a JOIN bundles b ON b.bundle_index = a.bundle_index
                WHERE a.parent = ? ORDER BY a.name COLLATE NOCASE LIMIT ? OFFSET ?
            """, (path, page_size, offset))]
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        return {
            "path": path, "dirs": dirs, "files": files,
            "filePage": {"page": page, "pageSize": page_size, "pages": max((total + page_size - 1) // page_size, 1), "total": total},
            "directory": dict(current), "meta": meta,
        }

    def asset(self, asset_index: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT a.*, b.name AS bundle_name FROM assets a
                JOIN bundles b ON b.bundle_index = a.bundle_index WHERE a.asset_index = ?
            """, (asset_index,)).fetchone()
        return dict(row) if row else None

    def assets_by_path(self, path: str) -> list[dict]:
        """Return every asset matching one normalized logical path.

        StringPathHash records and manifest paths do not use consistent casing, so
        identity matching must be case-insensitive. Returning all rows keeps hash
        collisions and malformed duplicate manifest entries visible to callers.
        """

        path = _normal_path(path)
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT a.asset_index AS assetIndex, a.path, a.parent, a.name,
                       a.bundle_index AS bundleIndex, b.name AS bundleName,
                       a.size, a.path_hash AS pathHash
                FROM assets a
                JOIN bundles b ON b.bundle_index = a.bundle_index
                WHERE a.path = ? COLLATE NOCASE
                ORDER BY a.asset_index
            """, (path,)).fetchall()
        return [dict(row) for row in rows]

    def assets_by_name(self, name: str) -> list[dict]:
        """Return assets with one exact filename, ignoring manifest path casing."""

        if not name or "/" in name or "\\" in name:
            raise ValueError(f"invalid asset name: {name!r}")
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT a.asset_index AS assetIndex, a.path, a.parent, a.name,
                       a.bundle_index AS bundleIndex, b.name AS bundleName,
                       a.size, a.path_hash AS pathHash
                FROM assets a
                JOIN bundles b ON b.bundle_index = a.bundle_index
                WHERE a.name = ? COLLATE NOCASE
                ORDER BY a.asset_index
            """, (name,)).fetchall()
        return [dict(row) for row in rows]

    def assets_in_directory(self, path: str) -> list[dict]:
        """Return every immediate asset in one exact manifest directory.

        资源集合端点需要完整、稳定地枚举一个目录，不能依赖分页 UI，也不能用
        模糊路径搜索。这里只返回直接子文件，调用方再按自身的命名规则筛选身份。
        """

        path = _normal_path(path).strip("/")
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT a.asset_index AS assetIndex, a.path, a.parent, a.name,
                       a.bundle_index AS bundleIndex, b.name AS bundleName,
                       a.size, a.path_hash AS pathHash
                FROM assets a
                JOIN bundles b ON b.bundle_index = a.bundle_index
                WHERE a.parent = ? COLLATE NOCASE
                ORDER BY a.name COLLATE NOCASE, a.asset_index
            """, (path,)).fetchall()
        return [dict(row) for row in rows]

    def search_animation_assets(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        query = query.strip()
        if page < 1 or page_size not in range(1, 201):
            raise ValueError("invalid animation search page")
        if not query:
            return {
                "query": "",
                "files": [],
                "page": page,
                "pageSize": page_size,
                "pages": 1,
                "total": 0,
            }
        pattern = f"%{_escape_like(query)}%"
        animation_filter = """
            (lower(a.path) LIKE '%.anim'
             OR (lower(a.path) LIKE '%/animations/%' AND lower(a.path) LIKE '%.fbx##%'))
        """
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM assets a WHERE {animation_filter} "
                "AND a.path LIKE ? ESCAPE '\\' COLLATE NOCASE",
                (pattern,),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT a.asset_index AS assetIndex, a.path, a.name, a.size,
                       a.bundle_index AS bundleIndex, b.name AS bundleName
                FROM assets a JOIN bundles b ON b.bundle_index = a.bundle_index
                WHERE {animation_filter}
                  AND a.path LIKE ? ESCAPE '\\' COLLATE NOCASE
                ORDER BY a.path COLLATE NOCASE, a.asset_index
                LIMIT ? OFFSET ?
                """,
                (pattern, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "query": query,
            "files": [dict(row) for row in rows],
            "page": page,
            "pageSize": page_size,
            "pages": max((total + page_size - 1) // page_size, 1),
            "total": total,
        }

    def bundle_dependencies(self, bundle_index: int, *, transitive: bool = True) -> list[dict]:
        """Return direct dependencies or their deterministic transitive closure."""

        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM bundles WHERE bundle_index = ?", (bundle_index,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"unknown bundle index: {bundle_index}")
            if not transitive:
                rows = conn.execute("""
                    SELECT b.bundle_index AS bundleIndex, b.name
                    FROM bundle_dependencies d
                    JOIN bundles b ON b.bundle_index = d.dependency_index
                    WHERE d.bundle_index = ? AND d.kind = 'direct'
                    ORDER BY b.bundle_index
                """, (bundle_index,)).fetchall()
                return [dict(row) for row in rows]

            rows = conn.execute("""
                WITH RECURSIVE closure(bundle_index) AS (
                    SELECT dependency_index FROM bundle_dependencies
                    WHERE bundle_index = ? AND kind = 'direct'
                    UNION
                    SELECT d.dependency_index
                    FROM bundle_dependencies d
                    JOIN closure c ON c.bundle_index = d.bundle_index
                    WHERE d.kind = 'direct'
                )
                SELECT b.bundle_index AS bundleIndex, b.name
                FROM closure c JOIN bundles b ON b.bundle_index = c.bundle_index
                WHERE b.bundle_index != ?
                ORDER BY b.bundle_index
            """, (bundle_index, bundle_index)).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict:
        with self._connect() as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        return {
            "bundleCount": int(meta["bundleCount"]),
            "assetCount": int(meta["assetCount"]),
        }
