import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from animestudio_animation import AnimationClipSelectionError


class ManifestModelBlendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.blender = self.root / "blender.exe"
        self.importer = self.root / "blender_import_model.py"
        self.backend = self.root / "blender_materials.py"
        self.lighting = self.root / "character_lighting.py"
        for path in (self.blender, self.importer, self.backend, self.lighting):
            path.write_bytes(b"tool")

    def tearDown(self):
        self.temporary.cleanup()

    def make_handler(self, asset_path):
        model_root = self.root / "model"
        model_root.mkdir(exist_ok=True)
        model_document = model_root / "model-document.json"
        glb = model_root / "model.glb"
        model_document.write_text("{}", encoding="utf-8")
        glb.write_bytes(b"glb")

        handler = object.__new__(server.BrowserHandler)
        handler.wfile = io.BytesIO()
        handler.resolve_manifest_asset_source = lambda _query: (
            object(),
            {"asset_index": 7, "path": asset_path},
            {},
            self.root / "source.chk",
        )
        lods = []

        def ensure_model(resolved, *, lod=0):
            lods.append(lod)
            return resolved[1], model_document, glb

        handler.ensure_manifest_asset_model_glb = ensure_model
        handler.send_response = lambda _status: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.send_error_json = lambda status, message: self.fail(
            f"unexpected HTTP {status}: {message}"
        )
        return handler, model_root, lods

    def run_blender(self, command, **_kwargs):
        Path(command[-1]).write_bytes(b"BLENDER-v404")

    def test_exports_avatar_mesh_with_requested_lod(self):
        handler, model_root, lods = self.make_handler(
            "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
            "data_npc_avatarmesh_qinjc.asset"
        )
        with (
            patch.object(server, "BLENDER_EXE", self.blender),
            patch.object(server, "BLENDER_MODEL_IMPORTER", self.importer),
            patch.object(server, "PROJECT_ROOT", self.root),
            patch.object(server.subprocess, "run", side_effect=self.run_blender),
        ):
            handler.handle_manifest_asset_model_blend({"lod": ["2"]})

        self.assertEqual([2], lods)
        self.assertEqual(b"BLENDER-v404", handler.wfile.getvalue())
        self.assertEqual(b"BLENDER-v404", (model_root / "model.blend").read_bytes())

    def test_cancelled_background_blend_does_not_start_blender(self):
        _handler, model_root, _lods = self.make_handler("assets/model.prefab")
        glb = model_root / "model.glb"
        cancel_event = threading.Event()
        cancel_event.set()
        with (
            patch.object(server, "BLENDER_EXE", self.blender),
            patch.object(server, "BLENDER_MODEL_IMPORTER", self.importer),
            patch.object(server, "PROJECT_ROOT", self.root),
            patch.object(server.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(RuntimeError, "worker_cancelled"):
                _handler.ensure_model_blend_file(glb, cancel_event=cancel_event)
        popen.assert_not_called()

    def test_background_blend_result_registers_private_artifact(self):
        handler, model_root, _lods = self.make_handler("assets/model.prefab")
        animated_glb = model_root / "animated.glb"
        animated_glb.write_bytes(b"glb")
        blend = model_root / "animated.blend"
        blend.write_bytes(b"blend")
        source = (
            object(),
            {"asset_index": 21, "path": "assets/idle.anim"},
            {},
            self.root / "idle.chk",
        )
        cancel_event = threading.Event()
        reports = []

        def ensure_animated(
            _resolved,
            selected,
            *,
            lod,
            skip_incompatible,
            cancel_event=None,
            progress=None,
        ):
            self.assertEqual(0, lod)
            self.assertFalse(skip_incompatible)
            self.assertIs(cancel_event, cancel_event_outer)
            progress({"stage": "animations", "completed": 1, "total": 1})
            return server.AnimatedModelBundle(
                {"asset_index": 7, "path": "assets/model.prefab"},
                [selected[0][1]],
                model_root / "model-document.json",
                animated_glb,
                [],
            )

        handler.ensure_animated_model_glb = ensure_animated
        handler.ensure_model_blend_file = lambda path, *, cancel_event=None: (
            blend
            if path == animated_glb and cancel_event is cancel_event_outer
            else self.fail("unexpected blend input")
        )
        cancel_event_outer = cancel_event

        result = handler.build_model_blend_task_result(
            handler.resolve_manifest_asset_source({}),
            [source],
            0,
            cancel_event=cancel_event,
            progress=reports.append,
        )

        self.assertTrue(result["artifactAvailable"])
        self.assertEqual(str(blend.resolve()), result["_artifactPath"])
        self.assertEqual("model-animations-1.blend", result["_artifactName"])
        self.assertEqual("blender", reports[-1]["stage"])

    def test_rejects_non_model_asset(self):
        handler, _model_root, _lods = self.make_handler(
            "assets/beyond/arts/effects/commonassets/vat/softbody/sample.exr"
        )
        errors = []
        handler.send_error_json = lambda status, message: errors.append((status, message))
        with patch.object(server, "BLENDER_EXE", self.blender):
            handler.handle_manifest_asset_model_blend({})

        self.assertEqual([(400, "resource is not a supported model entry")], errors)

    def test_exports_selected_animation_as_a_separate_blend(self):
        handler, model_root, _lods = self.make_handler(
            "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
            "data_npc_avatarmesh_qinjc.asset"
        )
        animation_root = model_root / "animations" / "99"
        animation_root.mkdir(parents=True)
        animated_glb = animation_root / "model.glb"
        animated_glb.write_bytes(b"animated-glb")
        animation_resolved = (
            object(),
            {"asset_index": 99, "path": "assets/animations/idle.anim"},
            {},
            self.root / "animation.chk",
        )
        handler.resolve_animation_sources = lambda _query: [animation_resolved]
        handler.ensure_animated_model_glb = lambda _model, animations, *, lod, skip_incompatible: server.AnimatedModelBundle(
            {"asset_index": 7, "path": "assets/model.prefab"},
            [animation[1] for animation in animations],
            model_root / "model-document.json",
            animated_glb,
            [],
        )
        with (
            patch.object(server, "BLENDER_EXE", self.blender),
            patch.object(server, "BLENDER_MODEL_IMPORTER", self.importer),
            patch.object(server, "PROJECT_ROOT", self.root),
            patch.object(server.subprocess, "run", side_effect=self.run_blender),
        ):
            handler.handle_manifest_asset_model_blend(
                {"animationAssetIndex": ["99"], "lod": ["0"]}
            )

        self.assertEqual(b"BLENDER-v404", handler.wfile.getvalue())
        self.assertTrue((animation_root / "model.blend").is_file())

    def test_passes_all_selected_animations_to_bundle_export(self):
        handler, model_root, _lods = self.make_handler("assets/model.prefab")
        animation_root = model_root / "animation-sets" / "selection"
        animation_root.mkdir(parents=True)
        animated_glb = animation_root / "model.glb"
        animated_glb.write_bytes(b"animated-glb")
        animations = [
            (
                object(),
                {"asset_index": index, "path": f"assets/{name}.anim"},
                {},
                self.root / f"{name}.chk",
            )
            for index, name in ((11, "idle"), (22, "walk"))
        ]
        captured = []
        handler.resolve_animation_sources = lambda _query: animations

        skip_modes = []

        def ensure_animations(_model, selected, *, lod, skip_incompatible):
            captured.extend(int(item[1]["asset_index"]) for item in selected)
            skip_modes.append(skip_incompatible)
            return server.AnimatedModelBundle(
                {"asset_index": 7, "path": "assets/model.prefab"},
                [item[1] for item in selected],
                model_root / "model-document.json",
                animated_glb,
                [],
            )

        handler.ensure_animated_model_glb = ensure_animations
        with (
            patch.object(server, "BLENDER_EXE", self.blender),
            patch.object(server, "BLENDER_MODEL_IMPORTER", self.importer),
            patch.object(server, "PROJECT_ROOT", self.root),
            patch.object(server.subprocess, "run", side_effect=self.run_blender),
        ):
            handler.handle_manifest_asset_model_blend(
                {"animationAssetIndex": ["22", "11"], "lod": ["0"]}
            )

        self.assertEqual([11, 22], captured)
        self.assertEqual([True], skip_modes)
        self.assertEqual(b"BLENDER-v404", handler.wfile.getvalue())

    def test_prepares_bundle_with_complete_issue_report_before_download(self):
        handler, model_root, _lods = self.make_handler("assets/model.prefab")
        animated_glb = model_root / "animation-sets" / "selection" / "model.glb"
        animated_glb.parent.mkdir(parents=True)
        animated_glb.write_bytes(b"animated-glb")
        animations = [
            (
                object(),
                {"asset_index": index, "path": f"assets/{name}.anim"},
                {},
                self.root / f"{name}.chk",
            )
            for index, name in ((11, "idle"), (22, "ui"))
        ]
        handler.resolve_animation_sources = lambda _query: animations
        handler.ensure_animated_model_glb = lambda *_args, **_kwargs: server.AnimatedModelBundle(
            {"asset_index": 7, "path": "assets/model.prefab"},
            [animations[0][1]],
            model_root / "model-document.json",
            animated_glb,
            [
                server.AnimationExportIssue(
                    22,
                    "assets/ui.anim",
                    "modelBinding",
                    "animation has no compatible tracks",
                )
            ],
        )
        responses = []
        handler.send_json = lambda payload, status=200, **_kwargs: responses.append(
            (status, payload)
        )

        with patch.object(server, "BLENDER_EXE", self.blender):
            handler.handle_manifest_asset_model_blend(
                {
                    "manifestId": ["451359"],
                    "assetIndex": ["7"],
                    "animationAssetIndex": ["11", "22"],
                    "prepare": ["1"],
                }
            )

        self.assertEqual(1, len(responses))
        status, payload = responses[0]
        self.assertEqual(200, status)
        self.assertEqual(2, payload["requestedCount"])
        self.assertEqual(1, payload["exportedCount"])
        self.assertEqual(22, payload["issues"][0]["assetIndex"])
        self.assertNotIn("prepare=", payload["downloadUrl"])

    def test_skips_animation_without_compatible_tracks_in_bundle(self):
        handler, model_root, _lods = self.make_handler("assets/model.prefab")
        model_document = model_root / "model-document.json"
        geometry = model_root / "geometry.bin"
        model_document.write_text("{}", encoding="utf-8")
        geometry.write_bytes(b"geometry")
        compatible_clip = self.root / "compatible.animation.json"
        incompatible_clip = self.root / "incompatible.animation.json"
        compatible_clip.write_text("{}", encoding="utf-8")
        incompatible_clip.write_text("{}", encoding="utf-8")
        model_resolved = (
            object(),
            {"asset_index": 7, "path": "assets/model.prefab"},
            {"id": 1},
            self.root / "model.chk",
        )
        sources = [
            (
                object(),
                {"asset_index": 11, "path": "assets/compatible.anim", "bundle_name": "a"},
                {},
                self.root / "compatible.chk",
            ),
            (
                object(),
                {"asset_index": 22, "path": "assets/ui.anim", "bundle_name": "b"},
                {},
                self.root / "incompatible.chk",
            ),
        ]
        handler.ensure_manifest_asset_model_glb = lambda _resolved, *, lod: (
            model_resolved[1],
            model_document,
            model_document,
        )
        handler.load_model_glb_inputs = lambda *_args, **_kwargs: (
            {"animations": [], "images": []},
            b"geometry",
            {},
        )
        handler.ensure_animation_clip_export = lambda _record, _chunk, asset: (
            {"compatible": asset["asset_index"] == 11},
            compatible_clip if asset["asset_index"] == 11 else incompatible_clip,
            {},
        )

        attached = []

        def attach(document, binary, clip, **_kwargs):
            attached.append(clip)
            if clip["compatible"]:
                document["animations"].append({"id": "animation:11"})
            return binary

        def build_glb(*_args, **_kwargs):
            return b"animated-glb"

        with (
            patch.object(server, "attach_animation_clip", new=attach),
            patch.object(server, "build_glb", new=build_glb),
            patch.object(server, "SHADER_ARCHIVE_ROOT", self.root / "missing-shaders"),
        ):
            bundle = handler.ensure_animated_model_glb(
                model_resolved,
                sources,
                lod=0,
                skip_incompatible=True,
            )
            cached = handler.ensure_animated_model_glb(
                model_resolved,
                sources,
                lod=0,
                skip_incompatible=True,
            )

        self.assertEqual([11], [asset["asset_index"] for asset in bundle.animations])
        self.assertEqual([22], [issue.asset_index for issue in bundle.issues])
        self.assertEqual(b"animated-glb", bundle.glb_path.read_bytes())
        self.assertEqual(bundle, cached)
        self.assertEqual(2, len(attached))

    def test_rejects_bundle_when_every_animation_is_incompatible(self):
        handler, model_root, _lods = self.make_handler("assets/model.prefab")
        model_document = model_root / "model-document.json"
        model_document.write_text("{}", encoding="utf-8")
        source_clip = self.root / "ui.animation.json"
        source_clip.write_text("{}", encoding="utf-8")
        model_resolved = (
            object(),
            {"asset_index": 7, "path": "assets/model.prefab"},
            {"id": 1},
            self.root / "model.chk",
        )
        sources = [
            (
                object(),
                {"asset_index": 22, "path": "assets/ui.anim", "bundle_name": "ui"},
                {},
                self.root / "ui.chk",
            )
        ]
        handler.ensure_manifest_asset_model_glb = lambda _resolved, *, lod: (
            model_resolved[1],
            model_document,
            model_document,
        )
        handler.load_model_glb_inputs = lambda *_args, **_kwargs: (
            {"animations": [], "images": []},
            b"geometry",
            {},
        )
        handler.ensure_animation_clip_export = lambda *_args: ({}, source_clip, {})

        with patch.object(
            server,
            "attach_animation_clip",
            new=lambda _document, binary, _clip, **_kwargs: binary,
        ):
            bundle = handler.ensure_animated_model_glb(
                model_resolved,
                sources,
                lod=0,
                skip_incompatible=True,
            )

        self.assertEqual([], bundle.animations)
        self.assertEqual([22], [issue.asset_index for issue in bundle.issues])

    def test_skips_unidentifiable_clip_in_bundle(self):
        handler, model_root, _lods = self.make_handler("assets/model.prefab")
        model_document = model_root / "model-document.json"
        model_document.write_text("{}", encoding="utf-8")
        (model_root / "geometry.bin").write_bytes(b"geometry")
        compatible_clip = self.root / "compatible.animation.json"
        compatible_clip.write_text("{}", encoding="utf-8")
        model_resolved = (
            object(),
            {"asset_index": 7, "path": "assets/model.prefab"},
            {"id": 1},
            self.root / "model.chk",
        )
        sources = [
            (
                object(),
                {"asset_index": index, "path": f"assets/{name}.anim", "bundle_name": name},
                {},
                self.root / f"{name}.chk",
            )
            for index, name in ((11, "compatible"), (22, "unidentifiable"))
        ]
        handler.ensure_manifest_asset_model_glb = lambda _resolved, *, lod: (
            model_resolved[1],
            model_document,
            model_document,
        )
        handler.load_model_glb_inputs = lambda *_args, **_kwargs: (
            {"animations": [], "images": []},
            b"geometry",
            {},
        )

        def ensure_clip(_record, _chunk, asset):
            if asset["asset_index"] == 22:
                raise AnimationClipSelectionError("no unique clip")
            return {}, compatible_clip, {}

        handler.ensure_animation_clip_export = ensure_clip

        def attach(document, binary, _clip, **_kwargs):
            document["animations"].append({"id": "animation:11"})
            return binary

        def build_glb(*_args, **_kwargs):
            return b"animated-glb"

        with (
            patch.object(server, "attach_animation_clip", new=attach),
            patch.object(server, "build_glb", new=build_glb),
            patch.object(server, "SHADER_ARCHIVE_ROOT", self.root / "missing-shaders"),
        ):
            bundle = handler.ensure_animated_model_glb(
                model_resolved,
                sources,
                lod=0,
                skip_incompatible=True,
            )

        self.assertEqual([11], [asset["asset_index"] for asset in bundle.animations])
        self.assertEqual([22], [issue.asset_index for issue in bundle.issues])

    def test_resolves_repeated_and_comma_separated_animation_indexes(self):
        handler = object.__new__(server.BrowserHandler)
        resolved_indexes = []

        def resolve(query):
            index = int(query["assetIndex"][0])
            resolved_indexes.append(index)
            return object(), {"asset_index": index}, {}, self.root / f"{index}.chk"

        handler.resolve_manifest_asset_source = resolve
        handler.send_error_json = lambda status, message: self.fail(
            f"unexpected HTTP {status}: {message}"
        )

        result = handler.resolve_animation_sources(
            {"animationAssetIndex": ["22", "11,22"]}
        )

        self.assertEqual([11, 22], resolved_indexes)
        self.assertEqual([11, 22], [item[1]["asset_index"] for item in result])


if __name__ == "__main__":
    unittest.main()
