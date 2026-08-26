using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ProjectileComponentPrefixDecoderTests
{
    [TestMethod]
    public void DecodesProjectileBlockLayerDefFromNativeBranchValues()
    {
        Assert.AreEqual(
            "Custom",
            ProjectileComponentPrefixDecoder.DecodeProjectileBlockLayerDef(-1)["name"]);
        Assert.AreEqual(
            "Nothing",
            ProjectileComponentPrefixDecoder.DecodeProjectileBlockLayerDef(0)["name"]);
        Assert.AreEqual(
            "WallAndGround",
            ProjectileComponentPrefixDecoder.DecodeProjectileBlockLayerDef(1)["name"]);
        Assert.IsFalse(
            ProjectileComponentPrefixDecoder.DecodeProjectileBlockLayerDef(2).ContainsKey("name"));
    }

    [TestMethod]
    public void ReadsGameplayTagQueryTagsAsRawTagIds()
    {
        var bytes = new byte[16];
        BitConverter.GetBytes(1).CopyTo(bytes, 0); // HasAll
        BitConverter.GetBytes(2).CopyTo(bytes, 4);
        BitConverter.GetBytes(unchecked((int)0x8d830993)).CopyTo(bytes, 8);
        BitConverter.GetBytes(42).CopyTo(bytes, 12);
        var reader = new ManagedReferencePayloadReader(bytes, 0, bytes.Length);

        var result = ProjectileComponentPrefixDecoder.ReadGameplayTagQuery(reader, "query");

        Assert.AreEqual(bytes.Length, reader.Position);
        var queryType = (Dictionary<string, object?>)result["queryType"]!;
        Assert.AreEqual("HasAll", queryType["name"]);
        var tags = (List<Dictionary<string, object?>>)result["tags"]!;
        Assert.AreEqual(2, tags.Count);
        Assert.AreEqual("0x8d830993", ((Dictionary<string, object?>)tags[0]["tagId"]!)["hex"]);
        Assert.AreEqual(42, ((Dictionary<string, object?>)tags[1]["tagId"]!)["value"]);
        Assert.IsFalse(tags[0].ContainsKey("path"));
    }

    [TestMethod]
    public void RejectsEntryWithDifferentRuntimeType()
    {
        var entry = new ManagedReferenceEntry(
            1,
            "AbilitySystemData",
            "Beyond.Gameplay.Core",
            "Gameplay.Beyond",
            0,
            0,
            0,
            false);

        var exception = Assert.ThrowsException<InvalidDataException>(() =>
            ProjectileComponentPrefixDecoder.Decode([], entry));

        StringAssert.Contains(exception.Message, "ProjectileComponentData");
    }

    [TestMethod]
    public void ReadsUniqueProjectileAbilitySystemEntityBlackboardList()
    {
        var bytes = new byte[96];
        BitConverter.GetBytes(1).CopyTo(bytes, 16);
        BitConverter.GetBytes(18).CopyTo(bytes, 20);
        "EntityBB_first_hit"u8.CopyTo(bytes.AsSpan(24));
        BitConverter.GetBytes(2.5d).CopyTo(bytes, 44);
        BitConverter.GetBytes(0).CopyTo(bytes, 52);
        BitConverter.GetBytes(1).CopyTo(bytes, 56);
        var entry = new ManagedReferenceEntry(
            1,
            "AbilitySystemData",
            "Beyond.Gameplay.Core",
            "Gameplay.Beyond",
            0,
            0,
            bytes.Length,
            false);

        var result = ProjectileAbilitySystemBlackboardDecoder.Decode(bytes, entry);

        Assert.IsNotNull(result);
        Assert.AreEqual(1, result.Count);
        Assert.AreEqual("EntityBB_first_hit", result[0]["key"]);
        Assert.AreEqual(2.5d, result[0]["valueDouble"]);
        Assert.AreEqual(string.Empty, result[0]["valueStr"]);
        Assert.AreEqual(true, result[0]["isDynamic"]);
    }
}
