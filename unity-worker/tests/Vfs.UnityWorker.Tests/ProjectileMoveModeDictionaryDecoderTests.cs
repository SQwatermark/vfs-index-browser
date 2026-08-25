using System.Buffers.Binary;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ProjectileMoveModeDictionaryDecoderTests
{
    [TestMethod]
    public void AcceptsEmptyDictionaryWithoutConsumingFollowingTail()
    {
        var bytes = new byte[16];

        var result = ProjectileMoveModeDictionaryDecoder.Decode(bytes, 0, bytes.Length);

        Assert.AreEqual(8, result.NextOffset);
        Assert.AreEqual(8, result.RemainingLength);
        Assert.AreEqual(0, result.Data["keyCount"]);
        Assert.AreEqual(0, result.Data["valueCount"]);
    }

    [TestMethod]
    public void RejectsDifferentKeyAndValueCounts()
    {
        var bytes = new byte[8];
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(4, 4), 1);

        var exception = Assert.ThrowsException<InvalidDataException>(() =>
            ProjectileMoveModeDictionaryDecoder.Decode(bytes, 0, bytes.Length));

        StringAssert.Contains(exception.Message, "数量不一致");
    }
}
