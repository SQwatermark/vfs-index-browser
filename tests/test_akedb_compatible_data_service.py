import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from akedb_compatible_data_service import (
    AkedbCompatibleDataError,
    AkedbCompatibleDataService,
)
from memorypack_value_decoder import MemoryPackValueDecoder


class FakeReader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def tell(self):
        return self.position


class FakeDecoder:
    def __init__(self, _schema, *, union_map):
        self.union_map = union_map

    def decode(self, reader, class_name):
        reader.position = len(reader.data)
        return {"class": class_name, "value": 7}


class AkedbCompatibleDataServiceTests(unittest.TestCase):
    def memorypack(self, *, decoder_type=FakeDecoder):
        return MemoryPackValueDecoder(
            lambda logical_id: "SampleData" if logical_id.endswith("sample.json") else None,
            lambda: (object(), {}),
            lambda _record, chunk: chunk.read_bytes(),
            FakeReader,
            decoder_type,
            None,
        )

    def test_table_uses_exact_logical_id_and_returns_data(self):
        requested = []
        service = AkedbCompatibleDataService(
            Path("unused.sqlite"),
            lambda logical_id: (
                requested.append(logical_id)
                or ({"id": 7}, Path("table.bytes"))
            ),
            lambda _record, _chunk: ({"data": {"rows": [1, 2]}}, b"raw"),
            self.memorypack(),
        )

        value = service.table("SampleTable")

        self.assertEqual({"rows": [1, 2]}, value)
        self.assertEqual(["Table/Data/TableCfg/SampleTable.bytes"], requested)

    def test_collection_manifest_filters_to_safe_effective_files(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vfs.sqlite"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE entries(scope TEXT, type TEXT, parent TEXT, name TEXT)"
                )
                connection.executemany(
                    "INSERT INTO entries VALUES (?, ?, ?, ?)",
                    [
                        ("effective", "file", "JsonData/Data/Json/SkillData", "b.json"),
                        ("effective", "file", "JsonData/Data/Json/SkillData", "a.json"),
                        ("effective", "file", "JsonData/Data/Json/SkillData", "bad!.json"),
                        ("Persistent", "file", "JsonData/Data/Json/SkillData", "old.json"),
                    ],
                )
                connection.commit()
            service = AkedbCompatibleDataService(
                database, lambda _logical_id: None, lambda *_args: None, self.memorypack()
            )

            result = service.collection_manifest("SkillData")

        self.assertEqual(
            [
                {"contentFile": "/api/akedb-compatible/SkillData/a.json"},
                {"contentFile": "/api/akedb-compatible/SkillData/b.json"},
            ],
            result,
        )

    def test_collection_file_requires_complete_memorypack_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            chunk = Path(directory) / "sample.json"
            chunk.write_bytes(b"abc")
            service = AkedbCompatibleDataService(
                Path("unused.sqlite"),
                lambda _logical_id: ({"id": 7}, chunk),
                lambda *_args: None,
                self.memorypack(),
            )

            value = service.collection_file("SkillData", "sample.json")

            self.assertEqual({"class": "SampleData", "value": 7}, value)

            class PartialDecoder(FakeDecoder):
                def decode(self, reader, class_name):
                    reader.position = 1
                    return {"class": class_name}

            partial = AkedbCompatibleDataService(
                Path("unused.sqlite"),
                lambda _logical_id: ({"id": 7}, chunk),
                lambda *_args: None,
                self.memorypack(decoder_type=PartialDecoder),
            )
            with self.assertRaises(AkedbCompatibleDataError) as raised:
                partial.collection_file("SkillData", "sample.json")

        self.assertEqual(422, raised.exception.status)
        self.assertIn("consumed 1 / 3", str(raised.exception))

    def test_missing_resource_is_not_found(self):
        service = AkedbCompatibleDataService(
            Path("unused.sqlite"),
            lambda _logical_id: None,
            lambda *_args: None,
            self.memorypack(),
        )
        with self.assertRaises(AkedbCompatibleDataError) as raised:
            service.collection_file("SkillData", "sample.json")
        self.assertEqual(404, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
