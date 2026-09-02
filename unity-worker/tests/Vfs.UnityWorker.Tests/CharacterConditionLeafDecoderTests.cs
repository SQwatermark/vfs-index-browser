using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class CharacterConditionLeafDecoderTests
{
    [TestMethod]
    public void ReadsContextTargetAndTagCountWithoutDroppingSignedTagIdentity()
    {
        var raw = Bytes(w =>
        {
            Target(w); w.Write(0); w.Write(1); w.Write(unchecked((int)0xa315eb9b));
            w.Write(0); w.Write(3); w.Write(0); w.Write(1f); Text(w, "");
        });
        var entry = Entry("CheckBuffStackNumByTag/Data", raw.Length, "Beyond.Gameplay.Core.Conditions");
        var data = CharacterConditionLeafDecoder.Decode(raw, entry)!;
        var target = (Dictionary<string, object?>)data["checkTarget"]!;
        Assert.AreEqual(2, target["targetSource"]);
        Assert.AreEqual("trigger", target["targetGroupKey"]);
        Assert.AreEqual(3, data["compareType"]);
        var tags = (List<Dictionary<string, object?>>)((Dictionary<string, object?>)data["tagQuery"]!)["tags"]!;
        Assert.AreEqual(unchecked((int)0xa315eb9b), ((Dictionary<string, object?>)tags[0]["tagId"]!)["value"]);
        Assert.ThrowsException<InvalidDataException>(() => CharacterConditionLeafDecoder.Decode(raw, entry with { DataLength = raw.Length - 4 }));
    }

    [TestMethod]
    public void ReadsObjectMaskAndPreservesNestedReferenceIds()
    {
        var raw = Bytes(w => { Target(w, 2708501211437859999); w.Write(16); });
        var data = CharacterConditionLeafDecoder.Decode(raw, Entry("CheckObjectTypeMatch/Data", raw.Length,
            "Beyond.Gameplay.Core.Conditions"))!;
        Assert.AreEqual(16, data["objectTypeMask"]);
        var selector = (Dictionary<string, object?>)((Dictionary<string, object?>)data["target"]!)["selectorData"]!;
        Assert.AreEqual("2708501211437859999", selector["finderData"]);
    }

    [TestMethod]
    public void EnumeratesOnlyPositiveManagedReferencesFromTargetSettings()
    {
        var raw = Bytes(w => { Target(w, 2708501211437859999); w.Write(16); });
        var data = CharacterConditionLeafDecoder.Decode(raw, Entry("CheckObjectTypeMatch/Data", raw.Length,
            "Beyond.Gameplay.Core.Conditions"))!;
        CollectionAssert.AreEqual(
            new long[] { 2708501211437859999 },
            CharacterConditionLeafDecoder.EnumerateManagedReferenceRids(data).ToArray());
    }

    [TestMethod]
    public void DecodesTangtangAdvancedEventBuffTagCondition()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6wMAAAEAAAAAAAAAAAAAAAEAAAAO5bV4AAAAAA==");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("CheckBuffIdInContextAdvanced/Data", raw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        Assert.AreEqual(1, data["checkType"]);
        Assert.AreEqual("", data["blackboardKey"]);
        Assert.AreEqual(0, ((List<Dictionary<string, object?>>)data["buffIdList"]!).Count);
        var query = (Dictionary<string, object?>)data["query"]!;
        Assert.AreEqual("HasAny", ((Dictionary<string, object?>)query["queryType"]!)["name"]);
        var tags = (List<Dictionary<string, object?>>)query["tags"]!;
        Assert.AreEqual(2025186574,
            ((Dictionary<string, object?>)tags[0]["tagId"]!)["value"]);
    }

    [TestMethod]
    public void DecodesAdvancedEventBuffIdsUsingBlackboardStringFieldOrder()
    {
        var raw = Bytes(w =>
        {
            w.Write(0); w.Write(1);
            w.Write(1); Text(w, "buff.literal"); Text(w, "buff_key");
            w.Write(0); w.Write(0); Text(w, "saved_buff");
        });
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("CheckBuffIdInContextAdvanced/Data", raw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        var buffId = ((List<Dictionary<string, object?>>)data["buffIdList"]!)[0];
        Assert.AreEqual(true, buffId["useKey"]);
        Assert.AreEqual("buff.literal", buffId["value"]);
        Assert.AreEqual("buff_key", buffId["key"]);
        Assert.AreEqual("saved_buff", data["blackboardKey"]);
    }

    [TestMethod]
    public void DebugPayloadIsPreservedEvenThoughNativeFallbackDoesNotReadIt()
    {
        var raw = Bytes(w =>
        {
            w.Write(1); Target(w); w.Write(1f); w.Write(0f); w.Write(0.5f); w.Write(1f);
            Text(w, "missing-key"); Text(w, "test-log");
        });
        var data = CharacterConditionLeafDecoder.Decode(raw, Entry("DebugPrintAction/Data", raw.Length))!;
        Assert.AreEqual("missing-key", data["bbKey"]);
        Assert.AreEqual("test-log", data["identifier"]);
        Assert.AreEqual(0.5f, ((Dictionary<string, object?>)data["color"]!)["b"]);
    }

    private static void Target(BinaryWriter w, long finder = -2)
    {
        w.Write(2); Text(w, "trigger"); w.Write(1); Text(w, ""); w.Write(0); Text(w, ""); w.Write(0);
        w.Write(finder); w.Write(0); w.Write(0); w.Write(0); w.Write(0);
        w.Write(-2L); w.Write(-2L); w.Write(0); w.Write(0); w.Write(0); w.Write(1); w.Write(0);
        w.Write(0); w.Write(0); Text(w, "");
    }

    [TestMethod]
    public void DecodesSavedKeyAndRejectsTrailingOrTruncatedBytes()
    {
        var raw = Bytes(w => { w.Write(15); Text(w, "EntityBB_test"); });
        var entry = Entry("CheckSpellInflictionType/Data", raw.Length, "Beyond.Gameplay.Core.Conditions");
        var data = CharacterConditionLeafDecoder.Decode(raw, entry)!;
        Assert.AreEqual(15, data["mask"]);
        Assert.AreEqual("EntityBB_test", data["savedKey"]);
        Assert.ThrowsException<InvalidDataException>(() => CharacterConditionLeafDecoder.Decode(raw, entry with { DataLength = raw.Length - 4 }));
        Assert.ThrowsException<InvalidDataException>(() => CharacterConditionLeafDecoder.Decode(raw.Concat(new byte[4]).ToArray(), entry with { DataLength = raw.Length + 4 }));
    }

    [TestMethod]
    public void DecodesCompareUsingUnityOrderAndSinglePrecisionValues()
    {
        var raw = Bytes(w =>
        {
            w.Write(1); w.Write(0.25f); Text(w, "EntityBB_test"); w.Write(0);
            w.Write(0); w.Write(1f); Text(w, "1");
        });
        var data = CharacterConditionLeafDecoder.Decode(raw, Entry("CompareFloat/Data", raw.Length))!;
        Assert.AreEqual(0, data["compare"]);
        var left = (Dictionary<string, object?>)data["valueA"]!;
        Assert.AreEqual(true, left["useKey"]);
        Assert.AreEqual(0.25f, left["value"]);
        Assert.AreEqual("EntityBB_test", left["key"]);
        Assert.AreEqual(1f, ((Dictionary<string, object?>)data["valueB"]!)["value"]);
    }

    [TestMethod]
    public void UnknownTypeOrAssemblyDoesNotPretendToDecode()
    {
        Assert.IsNull(CharacterConditionLeafDecoder.Decode([], Entry("Unknown/Data", 0)));
        Assert.IsNull(CharacterConditionLeafDecoder.Decode([], Entry("CompareFloat/Data", 0) with { AssemblyName = "Other" }));
    }

    [TestMethod]
    public void DecodesRealMainCharacterTargetWithoutDroppingTargetSettings()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6AMAAAIAAAAHAAAAdHJpZ2dlcgABAAAAAAAAAAAAAAAAAAAAAAAAAP7/////////AAAAAAAAAAAAAAAAAAAAAP7//////////v////////8AAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAA==");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("CheckMainCharacterCondition/Data", raw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        var target = (Dictionary<string, object?>)data["checkTarget"]!;
        Assert.AreEqual(2, target["targetSource"]);
        Assert.AreEqual("trigger", target["targetGroupKey"]);
    }

    [TestMethod]
    public void DecodesRealContextBuffDiscriminantIdsQueryAndOutputKey()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6QMAAAEAAAABAAAAKgAAAGJ1ZmZfY29tbW9uX2NyeXN0X3RyaWdnZXJlZF9waHlzaWNhbF9icmVhawAAAAAAAAEAAADScNxXAAAAAA==");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("CheckBuffIdInContext/Data", raw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        Assert.AreEqual(1, data["checkType"]);
        var ids = (List<Dictionary<string, object?>>)data["buffIdList"]!;
        Assert.AreEqual("buff_common_cryst_triggered_physical_break", ids[0]["buffId"]);
        Assert.AreEqual("", data["blackboardKey"]);
        Assert.AreEqual(1,
            ((List<Dictionary<string, object?>>)((Dictionary<string, object?>)data["query"]!)["tags"]!).Count);
    }

    [TestMethod]
    public void DecodesRealTagMatchAndHeaderOnlyNotNext()
    {
        var tagRaw = Convert.FromBase64String(
            "AAAAAAAAAAAAAAAA6QMAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAD+/////////wAAAAAAAAAAAAAAAAAAAAD+//////////7/////////AAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAOow7Xs=");
        var tag = CharacterConditionLeafDecoder.Decode(tagRaw,
            Entry("CheckTagMatch/Data", tagRaw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        Assert.IsTrue(tag.ContainsKey("checkTarget"));
        Assert.IsTrue(tag.ContainsKey("query"));

        var notRaw = Convert.FromBase64String("AQAAAAAAAAAAAAAA6QMAAA==");
        var notNext = CharacterConditionLeafDecoder.Decode(notRaw,
            Entry("NotNextCheckAction/Data", notRaw.Length))!;
        CollectionAssert.AreEquivalent(
            new[] { "isEnable", "priorityLevel", "priorityOffset", "serverActionIndex" },
            notNext.Keys.ToArray());
    }

    [TestMethod]
    public void DecodesRealComboHpThresholds()
    {
        var catcherRaw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6QMAAAIAAAAHAAAAdHJpZ2dlcgABAAAAAAAAAAAAAAAAAAAAAAAAAP7/////////AAAAAAAAAAAAAAAAAAAAAP7//////////v////////8AAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAM3MzD4AAAAA");
        var catcher = CharacterConditionLeafDecoder.Decode(catcherRaw,
            Entry("CheckHp/Data", catcherRaw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        Assert.AreEqual(0, catcher["compare"]);
        Assert.AreEqual(true, catcher["isRatio"]);
        Assert.AreEqual(0.4f, ((Dictionary<string, object?>)catcher["value"]!)["value"]);
        Assert.AreEqual("trigger",
            ((Dictionary<string, object?>)catcher["hpOwner"]!)["targetGroupKey"]);

        var snowshineRaw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6QMAAAIAAAAHAAAAdHJpZ2dlcgABAAAAAAAAAAAAAAAAAAAAAAAAAP7/////////AAAAAAAAAAAAAAAAAAAAAP7//////////v////////8AAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAJqZGT8AAAAA");
        var snowshine = CharacterConditionLeafDecoder.Decode(snowshineRaw,
            Entry("CheckHp/Data", snowshineRaw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        Assert.AreEqual(0.6f, ((Dictionary<string, object?>)snowshine["value"]!)["value"]);
    }

    [TestMethod]
    public void DecodesRealComboSingleBuffStackThreshold()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6gMAAAIAAAAHAAAAdHJpZ2dlcgABAAAAAAAAAAAAAAAAAAAAAAAAAP7/////////AAAAAAAAAAAAAAAAAAAAAP7//////////v////////8AAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAABYAAABidWZmX3BoeXNpY2FsX25vX2d1YXJkAAADAAAAAAAAAAAAQEAAAAAA");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("CheckBuffStackNum/Data", raw.Length,
                "Beyond.Gameplay.Core.Conditions"))!;
        Assert.AreEqual("buff_physical_no_guard",
            ((Dictionary<string, object?>)data["buffId"]!)["buffId"]);
        Assert.AreEqual(3, data["compareType"]);
        Assert.AreEqual(3f, ((Dictionary<string, object?>)data["value"]!)["value"]);
        Assert.ThrowsException<InvalidDataException>(() =>
            CharacterConditionLeafDecoder.Decode(raw,
                Entry("CheckBuffStackNum/Data", raw.Length - 4,
                    "Beyond.Gameplay.Core.Conditions")));
    }

    [TestMethod]
    public void DecodesRealComboCreateBuffActionWithoutDroppingLifecycleFields()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA7AMAAAEAAAAxAAAAYnVmZl9jaHJfMDAyMF9tZXVyc19zaWduYWxfd2Vha25lc3NfdHJpZ2dlcl9jb21ibwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAPwAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAA/v////////8AAAAAAAAAAAAAAAAAAAAA/v/////////+/////////wAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABQAAABtZXVyc193ZWFrbmVzc19lbmVteQAAAAAAAAAAAQAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("CreateBuffAction/Data", raw.Length))!;
        var buff = ((List<Dictionary<string, object?>>)data["buffs"]!)[0];
        Assert.AreEqual("buff_chr_0020_meurs_signal_weakness_trigger_combo", buff["buffId"]);
        Assert.AreEqual(false, buff["assignBlackboard"]);
        Assert.AreEqual(1f, ((Dictionary<string, object?>)data["count"]!)["value"]);
        Assert.AreEqual(0, ((Dictionary<string, object?>)data["targetSettings"]!)["targetSource"]);
        Assert.AreEqual(0, data["buffSource"]);
        Assert.AreEqual("meurs_weakness_enemy", data["contextKey"]);
        Assert.AreEqual(true, data["finishWithNextSkillIfNotInherited"]);
        Assert.AreEqual(true, data["inheritSourceSkillCastInfo"]);
        Assert.ThrowsException<InvalidDataException>(() =>
            CharacterConditionLeafDecoder.Decode(raw, Entry("CreateBuffAction/Data", raw.Length - 4)));
    }

    [TestMethod]
    public void DecodesRealComboStoreBuffCountAction()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA6wMAAAAAAAACAAAABwAAAHRyaWdnZXIAAQAAAAAAAAAAAAAAAAAAAAAAAAD+/////////wAAAAAAAAAAAAAAAAAAAAD+//////////7/////////AAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAQAAAAAAAAAWAAAAYnVmZl9waHlzaWNhbF9ub19ndWFyZAAAFgAAAEVudGl0eUJCX25vZ3VhcmRfY291bnQAAA==");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("StoreBuffCount/Data", raw.Length))!;
        Assert.AreEqual(false, data["useCurrentBuff"]);
        Assert.AreEqual("trigger",
            ((Dictionary<string, object?>)data["buffOwners"]!)["targetGroupKey"]);
        Assert.AreEqual("buff_physical_no_guard", data["buffId"]);
        Assert.AreEqual("EntityBB_noguard_count", data["blackboardKey"]);
        Assert.ThrowsException<InvalidDataException>(() =>
            CharacterConditionLeafDecoder.Decode(raw, Entry("StoreBuffCount/Data", raw.Length - 4)));
    }

    [TestMethod]
    public void DecodesRealComboIfElseAndEnumeratesItsChildActionReferences()
    {
        var raw = Convert.FromBase64String(
            "AQAAAAAAAAAAAAAA7AMAAAEAAAByA3hlgIeWJQAAAAAAAAAAAQAAAHMDeGWAh5YlAAAAAAAAAAABAAAAdAN4ZYCHliUAAAAAAAAAAAEAAAA=");
        var data = CharacterConditionLeafDecoder.Decode(raw,
            Entry("IfElseAction/IfElseActionData", raw.Length))!;
        Assert.AreEqual(true, data["alwaysNext"]);
        CollectionAssert.AreEqual(
            new[] { "2708501211437859698" },
            ((List<string>)((Dictionary<string, object?>)data["conditionAction"]!)["actionData"]!).ToArray());
        CollectionAssert.AreEqual(
            new long[]
            {
                2708501211437859698,
                2708501211437859699,
                2708501211437859700,
            },
            CharacterConditionLeafDecoder.EnumerateManagedReferenceRids(data).ToArray());
        Assert.ThrowsException<InvalidDataException>(() =>
            CharacterConditionLeafDecoder.Decode(raw,
                Entry("IfElseAction/IfElseActionData", raw.Length - 4)));
    }

    [TestMethod]
    public void DecodesHeaderOnlyReturnFalseAction()
    {
        var raw = Convert.FromBase64String("AQAAAAAAAAAAAAAA7QMAAA==");
        var data = CharacterConditionLeafDecoder.Decode(raw, Entry("ReturnFalseAction/Data", raw.Length))!;
        CollectionAssert.AreEquivalent(
            new[] { "isEnable", "priorityLevel", "priorityOffset", "serverActionIndex" },
            data.Keys.ToArray());
    }

    private static ManagedReferenceEntry Entry(string type, int size, string ns = "Beyond.Gameplay.Core") =>
        new(1, type, ns, "Gameplay.Beyond", 0, 0, size, false);
    private static byte[] Bytes(Action<BinaryWriter> write)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        writer.Write(1); writer.Write(0); writer.Write(0); writer.Write(1000);
        write(writer);
        return stream.ToArray();
    }
    private static void Text(BinaryWriter w, string text)
    {
        var bytes = Encoding.UTF8.GetBytes(text);
        w.Write(bytes.Length); w.Write(bytes);
        while (w.BaseStream.Position % 4 != 0) w.Write((byte)0);
    }
}
