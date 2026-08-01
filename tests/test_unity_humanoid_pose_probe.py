import unittest

from tools.build_unity_humanoid_pose_probe import build_probe_input


class UnityHumanoidPoseProbeTest(unittest.TestCase):
    def test_builds_independent_body_and_muscle_probes(self):
        source = {
            "name": "sample",
            "times": [0, 1],
            "curves": [{"name": "RootT.x"}],
            "applyFootIK": True,
            "applyPlayableIK": True,
        }

        result = build_probe_input(source, muscle_count=2)

        self.assertEqual(result["name"], "sample-pose-probes")
        self.assertEqual(result["times"], [])
        self.assertEqual(result["curves"], [])
        self.assertFalse(result["applyFootIK"])
        self.assertFalse(result["applyPlayableIK"])
        self.assertEqual(len(result["syntheticPoses"]), 17)
        self.assertEqual(result["syntheticPoses"][0]["name"], "neutral")
        self.assertEqual(
            [pose["muscleIndex"] for pose in result["syntheticPoses"] if pose["hasMuscle"]],
            [0, 0, 1, 1],
        )


if __name__ == "__main__":
    unittest.main()
