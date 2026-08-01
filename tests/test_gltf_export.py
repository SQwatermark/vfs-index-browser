import json
import struct
import unittest
from io import BytesIO

from PIL import Image

from gltf_export import build_glb


class GltfExportTests(unittest.TestCase):
    def test_exports_mesh_blend_shapes_as_gltf_morph_targets(self):
        document = {
            "asset": {"rootNodeIds": ["node:face"]},
            "bufferViews": [
                {
                    "id": f"view:{name}",
                    "bufferId": "buffer",
                    "byteOffset": index * 12,
                    "byteLength": 12,
                }
                for index, name in enumerate(("position", "delta-position", "delta-normal", "delta-tangent"))
            ],
            "accessors": [
                {
                    "id": name,
                    "bufferViewId": f"view:{name}",
                    "componentType": "f32",
                    "type": "vec3",
                    "count": 1,
                }
                for name in ("position", "delta-position", "delta-normal", "delta-tangent")
            ],
            "images": [],
            "textures": [],
            "materials": [],
            "meshes": [
                {
                    "id": "mesh:face",
                    "name": "Face_lod0",
                    "primitives": [
                        {"topology": "triangles", "attributes": {"POSITION": "position"}}
                    ],
                    "blendShapes": [
                        {
                            "name": "Blink",
                            "frames": [
                                {
                                    "weight": 100.0,
                                    "attributes": {
                                        "POSITION": "delta-position",
                                        "NORMAL": "delta-normal",
                                        "TANGENT": "delta-tangent",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            "nodes": [
                {
                    "id": "node:face",
                    "name": "Face_lod0",
                    "active": True,
                    "children": [],
                    "transform": {},
                    "meshId": "mesh:face",
                }
            ],
            "skins": [],
            "animations": [],
        }

        glb = build_glb(document, b"\0" * 48, lambda _image: b"")
        json_length = struct.unpack_from("<I", glb, 12)[0]
        payload = json.loads(glb[20:20 + json_length].decode("utf-8"))

        mesh = payload["meshes"][0]
        self.assertEqual(["Blink"], mesh["extras"]["targetNames"])
        self.assertEqual([100.0], mesh["extras"]["endfieldBlendShapeWeights"])
        self.assertEqual([0.0], mesh["weights"])
        self.assertEqual(
            {"POSITION", "NORMAL", "TANGENT"},
            set(mesh["primitives"][0]["targets"][0]),
        )

    def test_builds_self_contained_glb(self):
        document = {
            "asset": {"rootNodeIds": ["node:root"]},
            "bufferViews": [
                {"id": "view:positions", "bufferId": "buffer", "byteOffset": 0, "byteLength": 12},
                {"id": "view:unused", "bufferId": "buffer", "byteOffset": 12, "byteLength": 12},
                {"id": "view:times", "bufferId": "buffer", "byteOffset": 24, "byteLength": 8},
                {"id": "view:translations", "bufferId": "buffer", "byteOffset": 32, "byteLength": 24},
            ],
            "accessors": [
                {"id": "positions", "bufferViewId": "view:positions", "componentType": "f32", "type": "vec3", "count": 1},
                {"id": "unused", "bufferViewId": "view:unused", "componentType": "f32", "type": "vec3", "count": 1},
                {
                    "id": "animation-times",
                    "bufferViewId": "view:times",
                    "componentType": "f32",
                    "type": "scalar",
                    "count": 2,
                    "min": [0.0],
                    "max": [1.0],
                },
                {
                    "id": "animation-translations",
                    "bufferViewId": "view:translations",
                    "componentType": "f32",
                    "type": "vec3",
                    "count": 2,
                },
            ],
            "images": [
                {"id": "image", "mimeType": "image/png", "uri": "/image.png"},
                {"id": "packed-image", "mimeType": "image/png", "uri": "/packed.png"},
                {"id": "normal-image", "mimeType": "image/png", "uri": "/normal.png"},
                {"id": "unused-image", "mimeType": "image/png", "uri": "/unused.png"},
            ],
            "textures": [
                {"id": "texture", "imageId": "image"},
                {"id": "packed-texture", "imageId": "packed-image"},
                {"id": "normal-texture", "imageId": "normal-image"},
                {"id": "unused-texture", "imageId": "unused-image"},
            ],
            "materials": [
                {
                    "id": "material",
                    "name": "Material",
                    "sourceMaterial": {
                        "shader": "Character/Body",
                        "textureEnvironments": {
                            "_BaseMap": {
                                "textureId": "texture",
                                "scale": [1.0, 1.0],
                                "offset": [0.0, 0.0],
                            }
                        },
                        "ints": {"_Cull": 2},
                        "floats": {"_SilkStockingsMaxAffect": 0.9},
                        "colors": {
                            "_BaseColor": [1.0, 1.0, 1.0, 1.0],
                            "_SilkStockingsColor": [0.0, 0.0, 0.0, 1.0],
                        },
                    },
                    "previewPbr": {
                        "alphaMode": "BLEND",
                        "baseColorTextureId": "texture",
                        "baseColorTextureUsesGrayAsAlpha": True,
                        "diffuseRampTextureId": "packed-texture",
                        "sdfMaskTextureId": "packed-texture",
                        "shadowLutTextureId": "packed-texture",
                        "metallicGlossTextureId": "packed-texture",
                        "normalTextureId": "normal-texture",
                        "materialFamily": "characterNpr",
                        "materialRole": "skin",
                        "silkStockings": {"color": [0.0, 0.0, 0.0], "maxAffect": 0.9},
                        "overlayShadow": {"color": [0.3, 0.4, 0.5]},
                        "unlit": True,
                    },
                },
                {"id": "unused-material", "name": "Unused", "previewPbr": {"baseColorTextureId": "unused-texture"}},
            ],
            "meshes": [
                {"id": "mesh", "name": "Mesh", "primitives": [{"topology": "triangles", "attributes": {"POSITION": "positions"}, "materialId": "material"}]},
                {"id": "unused-mesh", "name": "Unused", "primitives": [{"topology": "triangles", "attributes": {"POSITION": "unused"}, "materialId": "unused-material"}]},
            ],
            "nodes": [
                {"id": "node:root", "name": "Root", "children": ["node:lod1", "node:shadow"], "transform": {}, "meshId": "mesh"},
                {"id": "node:lod1", "name": "Body_lod1", "children": [], "transform": {}, "meshId": "unused-mesh"},
                {"id": "node:shadow", "name": "Body_shadowProxyDesktop", "children": [], "transform": {}, "meshId": "unused-mesh"},
            ],
            "skins": [],
            "animations": [
                {
                    "id": "animation:idle",
                    "name": "Idle",
                    "duration": 1.0,
                    "channels": [
                        {
                            "targetId": "node:root",
                            "property": "translation",
                            "inputAccessorId": "animation-times",
                            "outputAccessorId": "animation-translations",
                            "interpolation": "linear",
                        },
                        {
                            "targetId": "material",
                            "property": "materialProperty",
                            "propertyName": "_Example",
                            "inputAccessorId": "animation-times",
                            "outputAccessorId": "animation-translations",
                            "interpolation": "linear",
                        },
                    ],
                }
            ],
        }

        source_images = {}
        for image_id, color in (
            ("image", (64, 255, 255, 255)),
            ("packed-image", (64, 128, 32, 192)),
            ("normal-image", (128, 64, 0, 255)),
        ):
            payload = BytesIO()
            Image.new("RGBA", (1, 1), color).save(payload, format="PNG")
            source_images[image_id] = payload.getvalue()
        geometry = (
            b"\0" * 24
            + struct.pack("<2f", 0.0, 1.0)
            + struct.pack("<6f", 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        )
        glb = build_glb(document, geometry, lambda image: source_images[image["id"]])

        magic, version, length = struct.unpack_from("<III", glb)
        self.assertEqual(0x46546C67, magic)
        self.assertEqual(2, version)
        self.assertEqual(len(glb), length)
        json_length, json_type = struct.unpack_from("<II", glb, 12)
        self.assertEqual(0x4E4F534A, json_type)
        payload = json.loads(glb[20:20 + json_length].decode("utf-8"))
        self.assertEqual("2.0", payload["asset"]["version"])
        self.assertEqual(
            "node:root",
            payload["nodes"][0]["extras"]["endfieldNodeId"],
        )
        self.assertEqual([-1.0, 1.0, 1.0], payload["nodes"][-1]["scale"])
        self.assertEqual(3, len(payload["images"]))
        self.assertEqual(1, len(payload["meshes"]))
        self.assertEqual(1, len(payload["materials"]))
        self.assertEqual("BLEND", payload["materials"][0]["alphaMode"])
        self.assertEqual(
            {
                "materialFamily": "characterNpr",
                "materialRole": "skin",
                "baseColorTextureId": "texture",
                "metallicGlossTextureId": "packed-texture",
                "diffuseRampTextureId": "packed-texture",
                "sdfMaskTextureId": "packed-texture",
                "shadowLutTextureId": "packed-texture",
                "silkStockings": {"color": [0.0, 0.0, 0.0], "maxAffect": 0.9},
                "overlayShadow": {"color": [0.3, 0.4, 0.5]},
            },
            payload["materials"][0]["extras"]["endfieldPreview"],
        )
        self.assertEqual(
            {
                "shader": "Character/Body",
                "textureEnvironments": {
                    "_BaseMap": {
                        "textureId": "texture",
                        "scale": [1.0, 1.0],
                        "offset": [0.0, 0.0],
                    }
                },
                "ints": {"_Cull": 2},
                "floats": {"_SilkStockingsMaxAffect": 0.9},
                "colors": {
                    "_BaseColor": [1.0, 1.0, 1.0, 1.0],
                    "_SilkStockingsColor": [0.0, 0.0, 0.0, 1.0],
                },
            },
            payload["materials"][0]["extras"]["endfieldSourceMaterial"],
        )
        self.assertEqual(
            ["KHR_materials_unlit", "KHR_texture_transform"],
            payload["extensionsUsed"],
        )
        self.assertEqual(
            {}, payload["materials"][0]["extensions"]["KHR_materials_unlit"]
        )
        pbr = payload["materials"][0]["pbrMetallicRoughness"]
        self.assertEqual(
            {"offset": [0.0, 1.0], "scale": [1.0, -1.0]},
            pbr["baseColorTexture"]["extensions"]["KHR_texture_transform"],
        )
        self.assertEqual(1.0, pbr["metallicFactor"])
        self.assertEqual(1.0, pbr["roughnessFactor"])
        self.assertIn("metallicRoughnessTexture", pbr)
        self.assertEqual(3, len(payload["accessors"]))
        self.assertEqual(12, payload["bufferViews"][0]["byteLength"])
        self.assertEqual(
            {
                "name": "Idle",
                "samplers": [
                    {
                        "input": 1,
                        "output": 2,
                        "interpolation": "LINEAR",
                    }
                ],
                "channels": [
                    {
                        "sampler": 0,
                        "target": {"node": 0, "path": "translation"},
                    }
                ],
            },
            payload["animations"][0],
        )
        binary_header = 20 + json_length
        binary_length, binary_type = struct.unpack_from("<II", glb, binary_header)
        self.assertEqual(0x004E4942, binary_type)
        binary = glb[binary_header + 8:binary_header + 8 + binary_length]
        pixels = []
        for image in payload["images"]:
            image_view = payload["bufferViews"][image["bufferView"]]
            image_payload = binary[
                image_view["byteOffset"]:image_view["byteOffset"] + image_view["byteLength"]
            ]
            pixels.append(Image.open(BytesIO(image_payload)).getpixel((0, 0)))
        self.assertEqual(
            [(255, 255, 255, 64), (255, 63, 64, 128), (128, 191, 238, 255)],
            pixels,
        )


if __name__ == "__main__":
    unittest.main()
