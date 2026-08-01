import math
import unittest

from animestudio_humanoid import (
    annotate_humanoid_bones,
    bake_humanoid_body_tracks,
    bake_humanoid_rotation_tracks,
    compute_humanoid_body_orientation,
)
from animestudio_animation import bind_animation_clip


def avatar_payload():
    return {
        "m_TOS": {"42": "Root/Bip001/Bip001_Spine"},
        "m_HumanDescription": {
            "m_Human": [
                {"m_BoneName": "Bip001_Spine", "m_HumanName": "Spine"},
            ]
        },
        "m_Avatar": {
            "m_Human": {
                "m_Skeleton": {
                    "m_Node": [{"m_ParentId": -1, "m_AxesId": 0}],
                    "m_ID": [42],
                    "m_AxesArray": [
                        {
                            "m_PreQ": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                            "m_PostQ": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                            "m_Sgn": {"X": 1, "Y": 1, "Z": 1},
                            "m_Limit": {
                                "m_Min": {"X": -1, "Y": -1, "Z": -1},
                                "m_Max": {"X": 1, "Y": 1, "Z": 1},
                            },
                        }
                    ],
                },
                "m_SkeletonPose": {
                    "m_X": [
                        {
                            "t": {"X": 0, "Y": 1, "Z": 0},
                            "q": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                        },
                    ]
                },
                "m_RootX": {
                    "t": {"X": 0, "Y": 1, "Z": 0},
                    "q": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                },
                "m_Scale": 1,
                "m_HumanBoneMass": [1 / 25] * 25,
            }
        },
    }


class AnimeStudioHumanoidTests(unittest.TestCase):
    def test_computes_body_orientation_from_hip_and_shoulder_landmarks(self):
        orientation = compute_humanoid_body_orientation(
            [-0.2, 0.0, 0.0],
            [0.2, 0.0, 0.0],
            [-0.5, 1.0, 0.0],
            [0.5, 1.0, 0.0],
        )

        self.assertEqual([0.0, 0.0, 0.0, 1.0], orientation)

    def test_body_orientation_rejects_degenerate_landmarks(self):
        with self.assertRaisesRegex(ValueError, "degenerate axis"):
            compute_humanoid_body_orientation(
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 1.0, 0.0],
            )

    def test_annotates_matching_skeleton_bone(self):
        document = {
            "skeletons": [
                {
                    "bones": [
                        {"id": "bone:spine", "name": "Bip001_Spine", "transform": {}},
                    ]
                }
            ]
        }

        attached = annotate_humanoid_bones(document, [avatar_payload()])

        self.assertEqual(1, attached)
        humanoid = document["skeletons"][0]["bones"][0]["extras"]["humanoid"]
        self.assertEqual("Spine", humanoid["humanBone"])
        self.assertEqual([0.0, 0.0, 0.0, 1.0], humanoid["preRotation"])
        self.assertEqual([-1.0, -1.0, -1.0], humanoid["limitMin"])
        self.assertEqual([0.0, 1.0, 0.0], humanoid["referenceTranslation"])
        avatar = document["skeletons"][0]["extras"]["humanoid"]
        self.assertEqual(1.0, avatar["humanScale"])
        self.assertEqual(1 / 25, avatar["boneMasses"]["Hips"])
        self.assertEqual(
            [0.0, 1.0, 0.0],
            avatar["rootTransform"]["translation"],
        )

    def test_bakes_muscle_values_to_rotation_track(self):
        document = {
            "skeletons": [
                {
                    "bones": [
                        {
                            "id": "bone:spine",
                            "name": "Bip001_Spine",
                            "transform": {},
                            "extras": {
                                "humanoid": {
                                    "humanBone": "Spine",
                                    "preRotation": [0, 0, 0, 1],
                                    "postRotation": [0, 0, 0, 1],
                                    "axisSign": [1, 1, 1],
                                    "limitMin": [-1, -1, -1],
                                    "limitMax": [1, 1, 1],
                                }
                            },
                        }
                    ]
                }
            ]
        }
        timelines = [[0.0, 1.0]]
        curves = [
            {
                "propertyName": "Spine Twist Left-Right",
                "timeline": 0,
                "values": [0.0, 1.0],
            }
        ]

        tracks, consumed = bake_humanoid_rotation_tracks(document, curves, timelines)

        self.assertEqual({0}, consumed)
        self.assertEqual("bone:spine", tracks[0]["targetId"])
        self.assertEqual([0.0, 0.0, 0.0, 1.0], tracks[0]["values"][0])
        self.assertAlmostEqual(math.sin(0.5), tracks[0]["values"][1][0])
        self.assertAlmostEqual(math.cos(0.5), tracks[0]["values"][1][3])

    def test_uses_unity_swing_twist_for_combined_axes(self):
        document = _humanoid_document(
            [("bone:spine", "Spine")],
        )
        timelines = [[0.0]]
        curves = [
            {"propertyName": "Spine Twist Left-Right", "timeline": 0, "values": [0.4]},
            {"propertyName": "Spine Left-Right", "timeline": 0, "values": [0.3]},
            {"propertyName": "Spine Front-Back", "timeline": 0, "values": [-0.2]},
        ]

        tracks, _ = bake_humanoid_rotation_tracks(document, curves, timelines)

        tx, ty, tz = math.tan(0.2), math.tan(0.15), math.tan(-0.1)
        expected = [tx, ty + tx * tz, tz - tx * ty, 1.0]
        length = math.sqrt(sum(value * value for value in expected))
        for actual, value in zip(tracks[0]["values"][0], expected):
            self.assertAlmostEqual(value / length, actual)

    def test_bakes_humanoid_node_outside_skin_joint_subset(self):
        document = _humanoid_document([])
        document["nodes"] = [{
            "id": "node:left-upper-leg",
            "name": "Bip001_L_Thigh",
            "transform": {},
            "extras": {
                "humanoid": {
                    "humanBone": "LeftUpperLeg",
                    "preRotation": [0, 0, 0, 1],
                    "postRotation": [0, 0, 0, 1],
                    "axisSign": [1, 1, 1],
                    "limitMin": [-1, -1, -1],
                    "limitMax": [1, 1, 1],
                }
            },
        }]
        curves = [{
            "propertyName": "Left Upper Leg Front-Back",
            "timeline": 0,
            "values": [0.0, 0.5],
        }]

        tracks, consumed = bake_humanoid_rotation_tracks(
            document,
            curves,
            [[0.0, 1.0]],
        )

        self.assertEqual({0}, consumed)
        self.assertEqual(["node:left-upper-leg"], [track["targetId"] for track in tracks])

    def test_bakes_endfield_leg_extension_muscles(self):
        document = _humanoid_document(
            [
                ("bone:left-foot", "LeftFoot"),
                ("bone:left-toes", "LeftToes"),
                ("bone:right-foot", "RightFoot"),
                ("bone:right-toes", "RightToes"),
            ]
        )
        curves = [
            {"propertyName": name, "timeline": 0, "values": [0.2]}
            for name in (
                "Left Foot Twist Roll",
                "Left Toes Left-Right",
                "Left Toes Twist Roll",
                "Right Foot Twist Roll",
                "Right Toes Left-Right",
                "Right Toes Twist Roll",
            )
        ]

        tracks, consumed = bake_humanoid_rotation_tracks(document, curves, [[0.0]])

        self.assertEqual(set(range(6)), consumed)
        self.assertEqual(
            {
                "bone:left-foot",
                "bone:left-toes",
                "bone:right-foot",
                "bone:right-toes",
            },
            {track["targetId"] for track in tracks},
        )

    def test_bakes_body_translation_to_hips_track(self):
        document = _body_document()
        curves = _body_curves(root_translation=[1.0, 2.0, 3.0])

        tracks, consumed = bake_humanoid_body_tracks(
            document,
            curves,
            [[0.0]],
            [],
        )

        by_property = {track["property"]: track for track in tracks}
        self.assertEqual(set(range(14)), consumed)
        self.assertEqual([[1.0, 2.0, 3.0]], by_property["translation"]["values"])
        self.assertEqual(
            [[0.0, 0.0, 0.0, 1.0]],
            by_property["rotation"]["values"],
        )

    def test_bakes_body_rotation_to_hips_track(self):
        document = _body_document()
        half = math.sqrt(0.5)
        curves = _body_curves(root_rotation=[0.0, 0.0, half, half])

        tracks, _ = bake_humanoid_body_tracks(document, curves, [[0.0]], [])

        rotation = next(track for track in tracks if track["property"] == "rotation")
        for actual, expected in zip(rotation["values"][0], [0.0, 0.0, half, half]):
            self.assertAlmostEqual(expected, actual)

    def test_redistributes_twist_to_child_bone(self):
        document = _humanoid_document(
            [
                ("bone:upper", "LeftUpperArm"),
                ("bone:lower", "LeftLowerArm"),
            ],
            twist_factors={"armTwist": 0.0},
        )
        timelines = [[0.0]]
        curves = [
            {"propertyName": "Left Arm Twist In-Out", "timeline": 0, "values": [0.6]},
            {"propertyName": "Left Forearm Stretch", "timeline": 0, "values": [0.0]},
        ]

        tracks, _ = bake_humanoid_rotation_tracks(document, curves, timelines)

        by_target = {track["targetId"]: track["values"][0] for track in tracks}
        self.assertEqual([0.0, 0.0, 0.0, 1.0], by_target["bone:upper"])
        self.assertAlmostEqual(math.sin(0.3), by_target["bone:lower"][0])
        self.assertAlmostEqual(math.cos(0.3), by_target["bone:lower"][3])

    def test_does_not_consume_curve_when_explicit_rotation_wins(self):
        document = {
            "skeletons": [
                {
                    "bones": [
                        {
                            "id": "bone:spine",
                            "extras": {
                                "humanoid": {
                                    "humanBone": "Spine",
                                    "preRotation": [0, 0, 0, 1],
                                    "postRotation": [0, 0, 0, 1],
                                    "axisSign": [1, 1, 1],
                                    "limitMin": [-1, -1, -1],
                                    "limitMax": [1, 1, 1],
                                }
                            },
                        }
                    ]
                }
            ]
        }
        curves = [
            {
                "propertyName": "Spine Front-Back",
                "timeline": 0,
                "values": [0.0],
            }
        ]

        tracks, consumed = bake_humanoid_rotation_tracks(
            document,
            curves,
            [[0.0]],
            excluded_target_ids={"bone:spine"},
        )

        self.assertEqual([], tracks)
        self.assertEqual(set(), consumed)

    def test_humanoid_baking_is_opt_in(self):
        document = {
            "skeletons": [
                {
                    "bones": [
                        {
                            "id": "bone:spine",
                            "extras": {
                                "humanoid": {
                                    "humanBone": "Spine",
                                    "preRotation": [0, 0, 0, 1],
                                    "postRotation": [0, 0, 0, 1],
                                    "axisSign": [1, 1, 1],
                                    "limitMin": [-1, -1, -1],
                                    "limitMax": [1, 1, 1],
                                }
                            },
                        }
                    ]
                }
            ],
            "nodes": [],
            "asset": {"rootNodeIds": []},
        }
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Idle",
            "duration": 0,
            "sampleRate": 60,
            "timelines": [[0]],
            "curves": [
                {
                    "property": "float",
                    "propertyName": "Spine Front-Back",
                    "timeline": 0,
                    "values": [0],
                }
            ],
        }

        stable = bind_animation_clip(document, clip, animation_id="stable", source={})
        experimental = bind_animation_clip(
            document,
            clip,
            animation_id="experimental",
            source={},
            bake_humanoid=True,
        )

        self.assertEqual([], stable["tracks"])
        self.assertEqual(1, len(experimental["tracks"]))


def _humanoid_document(bones, *, twist_factors=None):
    factors = {
        "armTwist": 0.5,
        "foreArmTwist": 0.5,
        "upperLegTwist": 0.5,
        "legTwist": 0.5,
        **(twist_factors or {}),
    }
    return {
        "skeletons": [
            {
                "bones": [
                    {
                        "id": bone_id,
                        "extras": {
                            "humanoid": {
                                "humanBone": human_bone,
                                "preRotation": [0, 0, 0, 1],
                                "postRotation": [0, 0, 0, 1],
                                "axisSign": [1, 1, 1],
                                "limitMin": [-1, -1, -1],
                                "limitMax": [1, 1, 1],
                                "twistFactors": factors,
                            }
                        },
                    }
                    for bone_id, human_bone in bones
                ]
            }
        ]
    }


def _body_document():
    landmarks = (
        ("bone:hips", "Hips", [0.0, 0.0, 0.0]),
        ("bone:left-leg", "LeftUpperLeg", [-0.2, 0.0, 0.0]),
        ("bone:right-leg", "RightUpperLeg", [0.2, 0.0, 0.0]),
        ("bone:left-arm", "LeftUpperArm", [-0.5, 1.0, 0.0]),
        ("bone:right-arm", "RightUpperArm", [0.5, 1.0, 0.0]),
    )
    return {
        "skeletons": [
            {
                "extras": {
                    "humanoid": {
                        "rootTransform": {
                            "translation": [0.0, 0.0, 0.0],
                            "rotation": [0.0, 0.0, 0.0, 1.0],
                        },
                        "humanScale": 1.0,
                        "centerOfMassOffset": [0.0, 0.0, 0.0],
                        "boneMasses": {"Hips": 1.0},
                    }
                },
                "bones": [
                    {
                        "id": bone_id,
                        "name": human_name,
                        "transform": {
                            "translation": translation,
                            "rotation": [0.0, 0.0, 0.0, 1.0],
                            "scale": [1.0, 1.0, 1.0],
                        },
                        "extras": {"humanoid": {"humanBone": human_name}},
                    }
                    for bone_id, human_name, translation in landmarks
                ],
            }
        ]
    }


def _body_curves(*, root_translation=None, root_rotation=None):
    values = {
        "MotionT": [0.0, 0.0, 0.0],
        "MotionQ": [0.0, 0.0, 0.0, 1.0],
        "RootT": root_translation or [0.0, 0.0, 0.0],
        "RootQ": root_rotation or [0.0, 0.0, 0.0, 1.0],
    }
    return [
        {
            "propertyName": f"{prefix}.{axis}",
            "timeline": 0,
            "values": [value],
        }
        for prefix, axes in (
            ("MotionT", "xyz"),
            ("MotionQ", "xyzw"),
            ("RootT", "xyz"),
            ("RootQ", "xyzw"),
        )
        for axis, value in zip(axes, values[prefix])
    ]


if __name__ == "__main__":
    unittest.main()
