using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class CharacterTemplateDecoderTests
{
    private const long Root = 2708501211437859795;
    private const long SystemRid = Root + 6;
    private const long ActionRid = Root + 40;

    [TestMethod]
    public void ReadsRootLinkedPrefixAndPreservesRawConditionsAndTail()
    {
        var result = CharacterTemplateDecoder.Decode(Character(), "CHR_TEST");
        Assert.AreEqual("partial", result["decodeStatus"]);
        var root = Map(result["root"]);
        Assert.AreEqual(Root.ToString(), root["rid"]);
        var system = Map(result["abilitySystem"]);
        var board = (List<Dictionary<string, object?>>)system["entityBlackboard"]!;
        Assert.AreEqual(4, board.Count);
        Assert.AreEqual(3.25d, board[3]["valueDouble"]);
        Assert.IsTrue((bool)board[3]["isDynamic"]!);
        var bundle = Map(system["skillDataBundle"]);
        var conditions = (List<Dictionary<string, object?>>)bundle["comboSkillConditions"]!;
        Assert.AreEqual(121, conditions[0]["comboSkillEvent"]);
        var sequence = Map(conditions[0]["comboSkillCheckAction"]);
        Assert.AreEqual(ActionRid.ToString(), ((List<string>)sequence["actionData"]!)[0]);
        var reference = Map(Map(result["conditionReferences"])[ActionRid.ToString()]);
        Assert.AreEqual("raw", reference["decodeStatus"]);
        CollectionAssert.AreEqual(new byte[] { 11, 22, 33, 44 },
            Convert.FromBase64String((string)reference["rawBase64"]!));
        CollectionAssert.AreEqual(new byte[] { 55, 66, 77, 88 },
            Convert.FromBase64String((string)result["abilitySystemTailBase64"]!));
        var buff = ((List<Dictionary<string, object?>>)system["dashBuff"]!)[0];
        var assign = ((List<Dictionary<string, object?>>)buff["assignItems"]!)[0];
        Assert.AreEqual(2.5f, assign["numericValue"]);
        Assert.AreEqual("中文", assign["stringValue"]);
    }

    [TestMethod]
    public void ReadsEmptyBoardAtFieldBoundaryInsteadOfScanningTail()
    {
        var prefix = Prefix(0);
        var decoy = Bytes(w => { w.Write(1); Pair(w, "EntityBB_decoy", 99d); });
        var bytes = prefix.Concat(decoy).ToArray();
        var result = AbilitySystemDataPrefixDecoder.Decode(bytes, Entry(bytes.Length));
        Assert.AreEqual(prefix.Length, result.NextOffset);
        Assert.AreEqual(decoy.Length, result.RemainingLength);
        Assert.AreEqual(0, ((List<Dictionary<string, object?>>)result.Data["entityBlackboard"]!).Count);
    }

    [TestMethod]
    public void RejectsTruncatedPrefixAndWrongEntryType()
    {
        var prefix = Prefix(4);
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilitySystemDataPrefixDecoder.Decode(prefix, Entry(prefix.Length - 4)));
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilitySystemDataPrefixDecoder.Decode(prefix, Entry(prefix.Length) with { Namespace = "Wrong" }));
    }

    [TestMethod]
    public void RejectsInvalidListCountAndBool()
    {
        var prefix = Prefix(4);
        BitConverter.GetBytes(-1).CopyTo(prefix, 8); // modeConfig.modes
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilitySystemDataPrefixDecoder.Decode(prefix, Entry(prefix.Length)));
        prefix = Prefix(4);
        BitConverter.GetBytes(2).CopyTo(prefix, prefix.Length - 4); // final DataPair.isDynamic
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilitySystemDataPrefixDecoder.Decode(prefix, Entry(prefix.Length)));
    }

    [TestMethod]
    public void RejectsWrongOwnerMissingComponentAndMissingConditionReference()
    {
        Assert.ThrowsException<InvalidDataException>(() => CharacterTemplateDecoder.Decode(Character(), "wrong"));
        Assert.ThrowsException<InvalidDataException>(() => CharacterTemplateDecoder.Decode(Character(component: 999), "chr_test"));
        Assert.ThrowsException<InvalidDataException>(() => CharacterTemplateDecoder.Decode(Character(action: 999), "chr_test"));
    }

    [TestMethod]
    public void RejectsRegistryAtUnrelatedOffsetAndIncompleteRoot()
    {
        Assert.ThrowsException<InvalidDataException>(() => CharacterTemplateDecoder.Decode(Character(gap: true), "chr_test"));
        Assert.ThrowsException<InvalidDataException>(() => CharacterTemplateDecoder.Decode(Character(extraRoot: true), "chr_test"));
    }

    private static Dictionary<string, object?> Map(object? value) => (Dictionary<string, object?>)value!;
    private static ManagedReferenceEntry Entry(int size) =>
        new(SystemRid, "AbilitySystemData", "Beyond.Gameplay.Core", "Gameplay.Beyond", 0, 0, size, false);

    private static byte[] Character(long component = SystemRid, long action = ActionRid,
        bool gap = false, bool extraRoot = false) => Bytes(w =>
    {
        w.Write(0); w.Write(0L); w.Write(1); w.Write(0); w.Write(0L);
        Text(w, "data_chr_test"); w.Write(Root);
        if (gap) w.Write(0);
        w.Write(2); w.Write(3);
        Header(w, Root, "CharacterTemplateData", "Beyond.Gameplay");
        Text(w, "chr_test"); Text(w, ""); w.Write(2); w.Write(0);
        w.Write(2.6f); w.Write(0f); w.Write(0); w.Write(0); w.Write(1f);
        w.Write(1); w.Write(component); Text(w, "anim/test"); w.Write(4); Text(w, ""); w.Write(0);
        if (extraRoot) w.Write(1);
        Header(w, SystemRid, "AbilitySystemData", "Beyond.Gameplay.Core");
        w.Write(Prefix(4, action)); w.Write(new byte[] { 55, 66, 77, 88 });
        Header(w, ActionRid, "UnknownAction/Data", "Beyond.Gameplay.Core");
        w.Write(new byte[] { 11, 22, 33, 44 });
    });

    // Synthetic fixture: no extracted game bytes. Distinct nonzero fields catch float/double and RID-width mistakes.
    private static byte[] Prefix(int pairCount, long action = ActionRid) => Bytes(w =>
    {
        w.Write(1.5f); w.Write(2.5f); w.Write(0); // shape, modes
        for (var i = 0; i < 6; i++) { w.Write(1); Text(w, $"skill_{i}"); }
        for (var i = 0; i < 5; i++) Text(w, $"id_{i}");
        w.Write(0); w.Write(1); w.Write(121); w.Write(1); w.Write(action);
        w.Write(0); w.Write(0); w.Write(0); // sequence flags, immediately
        w.Write(1); w.Write(1); Pair(w, "consumed_type", 0d);
        Text(w, "combo"); Text(w, ""); w.Write(0); w.Write(0); Text(w, "hud"); w.Write(0); w.Write(0);
        for (var i = 0; i < 26; i++) w.Write(0); // UI and damage text
        w.Write(1); Text(w, "dash"); w.Write(1); w.Write(1);
        Text(w, "key"); Text(w, "input"); w.Write(1); w.Write(0); w.Write(2.5f); Text(w, "中文");
        w.Write(0); w.Write(0); // other buff lists
        for (var i = 0; i < 4 + 2 + 9; i++) w.Write(0);
        Text(w, "effect"); w.Write(pairCount);
        for (var i = 0; i < pairCount; i++) Pair(w, $"EntityBB_value_{i}", i + 0.25d);
    });

    private static void Pair(BinaryWriter w, string key, double value)
    { Text(w, key); w.Write(value); Text(w, ""); w.Write(1); }
    private static void Header(BinaryWriter w, long rid, string type, string ns)
    { w.Write(rid); Text(w, type); Text(w, ns); Text(w, "Gameplay.Beyond"); }
    private static void Text(BinaryWriter w, string text)
    {
        var bytes = Encoding.UTF8.GetBytes(text);
        w.Write(bytes.Length); w.Write(bytes);
        while (w.BaseStream.Position % 4 != 0) w.Write((byte)0);
    }
    private static byte[] Bytes(Action<BinaryWriter> write)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        write(writer);
        return stream.ToArray();
    }
}
