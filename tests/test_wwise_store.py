import sqlite3
import unittest

from audio_package import AudioPackageBank, AudioPackageIndex, AudioPackageMedia
from audio_dialog_store import create_audio_dialog_schema
from tests.test_wwise_hirc import EVENT, MEDIA, bank_fixture
from wwise_hirc import parse_soundbank
from wwise_store import (
    event_media_ids,
    get_wwise_event,
    list_wwise_media,
    replace_wwise_package,
)


class WwiseStoreTests(unittest.TestCase):
    def test_persists_and_traverses_event_graph(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        graph = parse_soundbank(EVENT, bank_fixture())
        package = AudioPackageIndex(
            file_size=999,
            banks=(AudioPackageBank(EVENT, 100, 200, "chinese", True, graph),),
            media=(),
        )

        replace_wwise_package(conn, 832795, package)

        self.assertEqual(MEDIA, event_media_ids(conn, EVENT, pck_file_id=832795))
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM audio_banks").fetchone()[0])
        self.assertEqual(6, conn.execute("SELECT COUNT(*) FROM wwise_objects").fetchone()[0])

    def test_replacing_package_removes_stale_graph(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        graph = parse_soundbank(EVENT, bank_fixture())
        replace_wwise_package(
            conn,
            1,
            AudioPackageIndex(100, (AudioPackageBank(EVENT, 0, 10, None, False, graph),), ()),
        )
        replace_wwise_package(conn, 1, AudioPackageIndex(100, (), ()))

        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM wwise_objects").fetchone()[0])

    def test_schema_can_share_audio_dialog_database(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        create_audio_dialog_schema(conn)
        replace_wwise_package(conn, 1, AudioPackageIndex(100, (), ()))

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertIn("audio_dialog", tables)
        self.assertIn("wwise_relations", tables)

    def test_virtual_queries_keep_physical_media_identity(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        graph = parse_soundbank(EVENT, bank_fixture())
        media = tuple(
            AudioPackageMedia(item, 1000 + index * 100, 50, "sound", "sfx")
            for index, item in enumerate(MEDIA)
        )
        replace_wwise_package(
            conn,
            9,
            AudioPackageIndex(
                2000,
                (AudioPackageBank(EVENT, 10, 500, "sfx", False, graph),),
                media,
            ),
            logical_path="Audio/test.pck",
        )

        event = get_wwise_event(conn, 9, EVENT, EVENT)
        total, rows = list_wwise_media(
            conn,
            f"{MEDIA[0] & 0xff:02x}",
            limit=100,
            offset=0,
        )

        self.assertEqual(list(MEDIA), event["media_ids"])
        self.assertEqual(3, len(event["media"]))
        self.assertGreaterEqual(total, 1)
        self.assertEqual("Audio/test.pck", rows[0]["logical_path"])


if __name__ == "__main__":
    unittest.main()
