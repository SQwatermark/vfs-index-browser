import tempfile
import unittest
from pathlib import Path

from tools.index_il2cpp_types import filter_types, parse_dump


DUMP = """// header
namespace Beyond.Gameplay.Core
{
    // TypeToken: 0x2000001  // size: 0x28
    public class Skill : BaseSkill
    {
        // Fields
        private System.Boolean m_appliedCost;  // 0x10

        // Properties
        System.String skillId { get; /* RVA: 0x01000000 */ }

        // Methods
        // RVA: 0x01234560  token: 0x6000001
        public System.Boolean CheckCost() { }
        // RVA: -1  // abstract  token: 0x6000002
        public virtual System.Void Generic() { }
    }
}
"""

AI_DUMP = """# AI-FRIENDLY STRUCTURED DUMP
CLASS: Beyond.Gameplay.Core.Skill
TYPE:  class
TOKEN: 0x2002253
SIZE:  0xF8
EXTENDS: Beyond.Gameplay.Core.AbilityComponent
IMPLEMENTS: Beyond.Gameplay.Core.IAbilitySystemListener System.IDisposable
FIELDS:
  private           System.Boolean                  m_appliedCost  // 0xa8
PROPERTIES:
  skillId  get=0x06CA7398
METHODS:
  RVA=0x06CA72BC  token=0x600D26D  System.Boolean CheckCd()
  RVA=-1  token=0x600D26E  System.Void Generic()
END_CLASS
"""


class Il2CppTypeIndexTests(unittest.TestCase):
    def test_parses_type_members_and_rvas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Gameplay.Beyond.dll.cs"
            path.write_text(DUMP, encoding="utf-8")
            index = parse_dump(path)

        self.assertEqual(1, index["typeCount"])
        skill = index["types"][0]
        self.assertEqual("Beyond.Gameplay.Core", skill["namespace"])
        self.assertEqual("Skill", skill["name"])
        self.assertEqual("Beyond.Gameplay.Core.Skill", skill["qualifiedName"])
        self.assertEqual("normal-csharp", index["dumpFormat"])
        self.assertEqual(0x28, skill["size"])
        self.assertEqual("0x2000001", skill["token"])
        self.assertEqual("private System.Boolean m_appliedCost;", skill["fields"][0]["signature"])
        self.assertEqual("System.String skillId", skill["properties"][0]["signature"])
        self.assertEqual(0x01234560, skill["methods"][0]["rva"])
        self.assertIsNone(skill["methods"][1]["rva"])

    def test_filters_on_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dump.cs"
            path.write_text(DUMP, encoding="utf-8")
            index = parse_dump(path)

        self.assertEqual(1, filter_types(index, "checkcost")["typeCount"])
        self.assertEqual(0, filter_types(index, "damage")["typeCount"])

    def test_parses_ai_structured_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Gameplay.Beyond.ai.cs"
            path.write_text(AI_DUMP, encoding="utf-8")
            index = parse_dump(path)

        self.assertEqual("ai-structured", index["dumpFormat"])
        skill = index["types"][0]
        self.assertEqual("Beyond.Gameplay.Core", skill["namespace"])
        self.assertEqual("Skill", skill["name"])
        self.assertEqual("Beyond.Gameplay.Core.Skill", skill["qualifiedName"])
        self.assertEqual("Beyond.Gameplay.Core.AbilityComponent", skill["extends"])
        self.assertEqual(
            ["Beyond.Gameplay.Core.IAbilitySystemListener", "System.IDisposable"],
            skill["implements"],
        )
        self.assertEqual(0xF8, skill["size"])
        self.assertEqual("0xa8", skill["fields"][0]["offset"])
        self.assertEqual("skillId  get=0x06CA7398", skill["properties"][0]["signature"])
        self.assertEqual(0x06CA72BC, skill["methods"][0]["rva"])
        self.assertIsNone(skill["methods"][1]["rva"])


if __name__ == "__main__":
    unittest.main()
