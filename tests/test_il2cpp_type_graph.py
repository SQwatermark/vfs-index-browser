import unittest

from tools.build_il2cpp_type_graph import build_graph, select_subgraph


INDEX = {
    "format": "Il2CppTypeIndex",
    "source": "sample.cs",
    "types": [
        {
            "qualifiedName": "Game.Skill",
            "token": "0x1",
            "size": 32,
            "fields": [
                {"signature": "private Game.CastData m_castData"},
                {"signature": "private System.String m_name"},
            ],
            "properties": [],
            "methods": [
                {"signature": "Game.Result Cast(Game.Target target)"},
            ],
        },
        {
            "qualifiedName": "Game.CastData",
            "token": "0x2",
            "size": 16,
            "fields": [{"signature": "private Game.CostData cost"}],
            "properties": [],
            "methods": [],
        },
        {
            "qualifiedName": "Game.CostData",
            "token": "0x3",
            "size": 8,
            "fields": [],
            "properties": [],
            "methods": [],
        },
        {
            "qualifiedName": "Game.Result",
            "token": "0x4",
            "size": 4,
            "fields": [],
            "properties": [],
            "methods": [],
        },
    ],
}


class Il2CppTypeGraphTests(unittest.TestCase):
    def test_builds_only_references_to_known_qualified_types(self):
        graph = build_graph(INDEX)

        self.assertEqual(4, graph["nodeCount"])
        references = {
            (edge["source"], edge["target"], edge["memberKind"])
            for edge in graph["edges"]
        }
        self.assertEqual(
            {
                ("Game.Skill", "Game.CastData", "field"),
                ("Game.Skill", "Game.Result", "method"),
                ("Game.CastData", "Game.CostData", "field"),
            },
            references,
        )

    def test_selects_outgoing_subgraph_by_depth(self):
        graph = build_graph(INDEX)

        depth_one = select_subgraph(graph, ["Game.Skill"], 1)
        depth_two = select_subgraph(graph, ["Game.Skill"], 2)

        self.assertEqual(
            {"Game.Skill", "Game.CastData", "Game.Result"},
            {node["id"] for node in depth_one["nodes"]},
        )
        self.assertIn("Game.CostData", {node["id"] for node in depth_two["nodes"]})

    def test_rejects_unknown_root(self):
        with self.assertRaisesRegex(ValueError, "unknown root types"):
            select_subgraph(build_graph(INDEX), ["Game.Missing"], 1)


if __name__ == "__main__":
    unittest.main()

