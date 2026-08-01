import unittest

from tools.build_unity_humanoid_oracle_input import build_input


class UnityHumanoidOracleInputTests(unittest.TestCase):
    def test_preserves_avatar_mapping_and_animator_float_curves(self):
        model = {
            "nodes": [
                {
                    "id": "root",
                    "name": "Bip001",
                    "transform": {
                        "translation": [0, 1, 2],
                        "rotation": [0, 0, 0, 1],
                        "scale": [1, 1, 1],
                    },
                }
            ]
        }
        avatar = {
            "m_TOS": {"1": "Root/Bip001"},
            "m_Avatar": {
                "m_Human": {
                    "m_Scale": 0.99,
                    "m_Skeleton": {
                        "m_Node": [{"m_ParentId": -1}],
                        "m_ID": [1],
                    },
                    "m_SkeletonPose": {
                        "m_X": [
                            {
                                "t": {"X": 0, "Y": 1, "Z": 2},
                                "q": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                                "s": {"X": 1, "Y": 1, "Z": 1},
                            }
                        ]
                    },
                }
            },
            "m_HumanDescription": {
                "m_Human": [
                    {
                        "m_BoneName": "Bip001",
                        "m_HumanName": "Hips",
                        "m_Limit": {
                            "m_Min": {"X": 0, "Y": 0, "Z": 0},
                            "m_Max": {"X": 0, "Y": 0, "Z": 0},
                            "m_Value": {"X": 0, "Y": 0, "Z": 0},
                            "m_Length": 0,
                            "m_Modified": False,
                        },
                    }
                ],
                "m_ArmTwist": 1,
                "m_ForeArmTwist": 0,
                "m_UpperLegTwist": 1,
                "m_LegTwist": 0,
                "m_ArmStretch": 0,
                "m_LegStretch": 0,
                "m_FeetSpacing": 0,
                "m_HasTranslationDoF": False,
            }
        }
        animation = {
            "name": "attack",
            "sampleRate": 60,
            "timelines": [[0, 1]],
            "curves": [
                {
                    "property": "float",
                    "propertyName": "Spine Front-Back",
                    "classId": 95,
                    "timeline": 0,
                    "values": [0.1, 0.2],
                },
                {
                    "property": "float",
                    "propertyName": "ignored",
                    "classId": 1,
                    "timeline": 0,
                    "values": [1, 2],
                },
            ],
        }

        result = build_input(model, avatar, animation)

        self.assertEqual("Hips", result["humanBones"][0]["humanName"])
        self.assertEqual("Bip001", result["humanBones"][0]["boneName"])
        self.assertEqual(["Spine Front-Back"], [curve["name"] for curve in result["curves"]])
        self.assertEqual("Bip001", result["avatarPose"][0]["name"])
        self.assertEqual([0.0, 1.0], result["times"])
        self.assertFalse(result["applyFootIK"])
        self.assertFalse(result["applyPlayableIK"])
        self.assertFalse(result["sampleAllBones"])
        self.assertEqual(0.99, result["avatar"]["humanScale"])


if __name__ == "__main__":
    unittest.main()
