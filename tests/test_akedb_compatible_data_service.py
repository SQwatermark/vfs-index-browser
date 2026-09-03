import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from endaxis_data_service import (
    EndaxisDataError,
    EndaxisDataService,
    make_endaxis_json_value,
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


class EndaxisDataServiceTests(unittest.TestCase):
    def test_non_finite_unity_values_use_strict_json_strings(self):
        self.assertEqual(
            {"values": ["Infinity", "-Infinity", "NaN", 1.5]},
            make_endaxis_json_value(
                {"values": [float("inf"), float("-inf"), float("nan"), 1.5]}
            ),
        )

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
        service = EndaxisDataService(
            Path("unused.sqlite"),
            lambda logical_id: (
                requested.append(logical_id)
                or ({"id": 7}, Path("table.bytes"))
            ),
            lambda _record, _chunk: ({"data": {"rows": [1, 2]}}, b"raw"),
            self.memorypack(),
            lambda _record, _chunk: b"binary",
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
            service = EndaxisDataService(
                database,
                lambda _logical_id: None,
                lambda *_args: None,
                self.memorypack(),
                lambda _record, _chunk: b"binary",
            )

            result = service.collection_manifest("SkillData")

        self.assertEqual(
            [
                {"contentFile": "/api/endaxis-data/SkillData/a.json"},
                {"contentFile": "/api/endaxis-data/SkillData/b.json"},
            ],
            result,
        )

    def test_collection_file_requires_complete_memorypack_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            chunk = Path(directory) / "sample.json"
            chunk.write_bytes(b"abc")
            service = EndaxisDataService(
                Path("unused.sqlite"),
                lambda _logical_id: ({"id": 7}, chunk),
                lambda *_args: None,
                self.memorypack(),
                lambda _record, _chunk: b"binary",
            )

            value = service.collection_file("SkillData", "sample.json")

            self.assertEqual({"class": "SampleData", "value": 7}, value)

            class PartialDecoder(FakeDecoder):
                def decode(self, reader, class_name):
                    reader.position = 1
                    return {"class": class_name}

            partial = EndaxisDataService(
                Path("unused.sqlite"),
                lambda _logical_id: ({"id": 7}, chunk),
                lambda *_args: None,
                self.memorypack(decoder_type=PartialDecoder),
                lambda _record, _chunk: b"binary",
            )
            with self.assertRaises(EndaxisDataError) as raised:
                partial.collection_file("SkillData", "sample.json")

        self.assertEqual(422, raised.exception.status)
        self.assertIn("consumed 1 / 3", str(raised.exception))

    def test_missing_resource_is_not_found(self):
        service = EndaxisDataService(
            Path("unused.sqlite"),
            lambda _logical_id: None,
            lambda *_args: None,
            self.memorypack(),
            lambda _record, _chunk: b"binary",
        )
        with self.assertRaises(EndaxisDataError) as raised:
            service.collection_file("SkillData", "sample.json")
        self.assertEqual(404, raised.exception.status)

    def test_collection_file_reads_plain_json_without_memorypack_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            chunk = Path(directory) / "plain.json"
            chunk.write_bytes(b'{"value": 9}')
            service = EndaxisDataService(
                Path("unused.sqlite"),
                lambda _logical_id: ({"id": 7}, chunk),
                lambda *_args: None,
                self.memorypack(),
                lambda _record, path: path.read_bytes(),
            )

            self.assertEqual(
                {"value": 9},
                service.collection_file("GameplayConfig", "plain.json"),
            )


if __name__ == "__main__":
    unittest.main()
