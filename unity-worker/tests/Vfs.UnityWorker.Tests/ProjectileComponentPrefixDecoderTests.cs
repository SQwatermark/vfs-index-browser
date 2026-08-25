using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ProjectileComponentPrefixDecoderTests
{
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
}
