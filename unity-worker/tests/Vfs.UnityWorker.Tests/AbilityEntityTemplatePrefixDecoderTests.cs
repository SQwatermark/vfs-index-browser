using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class AbilityEntityTemplatePrefixDecoderTests
{
    [TestMethod]
    public void PreservesExactComponentIdentityAndUnconsumedTail()
    {
        const long rid = 3010137844548109172;
        var bytes = Create(rid);
        var result = AbilityEntityTemplatePrefixDecoder.Decode(bytes, Entry(bytes.Length));
        Assert.AreEqual(rid, result.ComponentRids.Single());
        CollectionAssert.AreEqual(new[] { rid.ToString(System.Globalization.CultureInfo.InvariantCulture) },
            (string[])result.Data["componentList"]!);
        Assert.AreEqual("实体", result.Data["name"]);
        Assert.AreEqual(4, result.RemainingLength);
        Assert.AreEqual(bytes.Length - 4, result.NextOffset);
    }

    [TestMethod]
    public void CannotReadIntoTheNextRegistryEntry()
    {
        var bytes = Create(12);
        // 底层数组仍有完整字段；条目边界内缺少 maxDurationForServer 时也必须拒绝。
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilityEntityTemplatePrefixDecoder.Decode(bytes, Entry(bytes.Length - 8)));
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilityEntityTemplatePrefixDecoder.Decode(bytes, Entry(bytes.Length) with { ClassName = "Other" }));
    }

    [DataTestMethod]
    [DataRow(0L)]
    [DataRow(-1L)]
    [DataRow(-2L)]
    public void RejectsReservedComponentReferences(long rid)
    {
        var bytes = Create(rid);
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilityEntityTemplatePrefixDecoder.Decode(bytes, Entry(bytes.Length)));
    }

    private static ManagedReferenceEntry Entry(int length) =>
        new(10, "AbilityEntityTemplateData", "Beyond.Gameplay", "Gameplay.Beyond", 0, 0, length, false);

    private static byte[] Create(long rid)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
        void Text(string value)
        {
            var bytes = Encoding.UTF8.GetBytes(value);
            writer.Write(bytes.Length);
            writer.Write(bytes);
            while (stream.Position % 4 != 0) writer.Write((byte)0);
        }
        Text("entity"); Text("实体");
        writer.Write(2); // faction
        writer.Write(1); writer.Write(123); // bornTag
        writer.Write(0f); writer.Write(0f); // recycle delays
        writer.Write(0); writer.Write(1); writer.Write(0.5f); // fade settings
        writer.Write(1); writer.Write(rid);
        writer.Write(1); // maxStackingCnt
        writer.Write(0); writer.Write(1); Text(""); // maxStackingCntBB
        writer.Write(1); writer.Write(3f); // lifeType, duration
        writer.Write(1); writer.Write(0f); Text("duration"); // durationBB
        writer.Write(0f); // maxDurationForServer
        writer.Write(123456); // 未解码后段
        return stream.ToArray();
    }
}
