import struct
import unittest

from wwise_hirc import WwiseFormatError, normalize_wwise_id, parse_soundbank


EVENT = 3537164
ACTION = 515896377
CONTAINER = 958839230
SOUNDS = (270028408, 575070129, 984216163)
MEDIA = (327131060, 402315396, 513470926)


def hirc_object(object_type, payload):
    return bytes([object_type]) + struct.pack("<I", len(payload)) + payload


def sound(object_id, media_id):
    # v150 AkBankSourceData begins with ID, plugin, stream type and source ID.
    return struct.pack("<IIBII", object_id, 0x40001, 0, media_id, 4093) + b"\x01"


def bank_fixture():
    objects = [hirc_object(2, sound(item, media)) for item, media in zip(SOUNDS, MEDIA)]
    container = struct.pack("<I", CONTAINER) + b"\x00" * 12
    container += struct.pack("<I", len(SOUNDS)) + struct.pack("<3I", *SOUNDS)
    container += struct.pack("<H", len(SOUNDS))
    container += b"".join(struct.pack("<Ii", item, 50000) for item in SOUNDS)
    objects.append(hirc_object(5, container))
    action = struct.pack("<IHIB", ACTION, 0x0403, CONTAINER, 0)
    objects.append(hirc_object(3, action))
    event = struct.pack("<IBI", EVENT, 1, ACTION)
    objects.append(hirc_object(4, event))
    hirc = struct.pack("<I", len(objects)) + b"".join(objects)
    bkhd = struct.pack("<IIII", 150, EVENT, 0, 0)
    return b"BKHD" + struct.pack("<I", len(bkhd)) + bkhd + b"HIRC" + struct.pack("<I", len(hirc)) + hirc


def pck_fixture():
    bank = bank_fixture()
    language = struct.pack("<III", 1, 12, 1) + b"sfx\0"
    bank_sector_size = 24
    header_size = 28 + len(language) + bank_sector_size
    bank_row = struct.pack("<IIIII", EVENT, 0, len(bank), header_size, 1)
    banks = struct.pack("<I", 1) + bank_row
    header = b"AKPK" + struct.pack(
        "<IIIIII", header_size, 1, len(language), len(banks), 0, 0
    )
    return header + language + banks + bank


class WwiseHircTests(unittest.TestCase):
    def test_normalizes_signed_game_ids(self):
        self.assertEqual(0xF0000001, normalize_wwise_id(-0x0FFFFFFF))

    def test_recovers_audio_dialog_event_to_media_graph(self):
        graph = parse_soundbank(EVENT, bank_fixture())

        self.assertEqual(6, len(graph.objects))
        self.assertEqual(MEDIA, graph.media_ids_for_event(EVENT))
        self.assertIn(
            ("event", EVENT, "triggers", "action", ACTION),
            [
                (edge.source_kind, edge.source_id, edge.relation, edge.target_kind, edge.target_id)
                for edge in graph.relations
            ],
        )

    def test_rejects_trailing_hirc_bytes(self):
        payload = bank_fixture()
        hirc_pos = payload.index(b"HIRC")
        size = struct.unpack_from("<I", payload, hirc_pos + 4)[0]
        broken = bytearray(payload)
        struct.pack_into("<I", broken, hirc_pos + 4, size + 1)
        broken.append(0)

        with self.assertRaisesRegex(WwiseFormatError, "trailing"):
            parse_soundbank(EVENT, bytes(broken))

    def test_preserves_object_and_reports_unsupported_relation_layout(self):
        malformed = hirc_object(5, struct.pack("<I", CONTAINER) + b"\0" * 12)
        hirc = struct.pack("<I", 1) + malformed
        bkhd = struct.pack("<IIII", 150, EVENT, 0, 0)
        payload = (
            b"BKHD" + struct.pack("<I", len(bkhd)) + bkhd
            + b"HIRC" + struct.pack("<I", len(hirc)) + hirc
        )

        graph = parse_soundbank(EVENT, payload)

        self.assertEqual(1, len(graph.objects))
        self.assertEqual(0, len(graph.relations))
        self.assertEqual(CONTAINER, graph.diagnostics[0].object_id)
        self.assertIn("structural child lists", graph.diagnostics[0].message)


if __name__ == "__main__":
    unittest.main()
