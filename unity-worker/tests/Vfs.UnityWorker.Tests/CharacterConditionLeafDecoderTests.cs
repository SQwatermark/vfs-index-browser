using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class CharacterConditionLeafDecoderTests
{
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
