using System.Buffers.Binary;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ManagedReferencePayloadReaderTests
{
    [TestMethod]
    public void ReadsPrimitiveAndAlignedStringWithinEntryBoundary()
    {
        var bytes = new byte[20];
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(0, 4), 1);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(4, 4), 3);
        bytes[8] = (byte)'a';
        bytes[9] = (byte)'b';
        bytes[10] = (byte)'c';
        BinaryPrimitives.WriteInt64LittleEndian(bytes.AsSpan(12, 8), 37);
        var reader = new ManagedReferencePayloadReader(bytes, 0, bytes.Length);

        Assert.IsTrue(reader.ReadBool32("enabled"));
        Assert.AreEqual("abc", reader.ReadAlignedAsciiString("id"));
        Assert.AreEqual(37, reader.ReadInt64("rid"));
        reader.EnsureComplete();
    }

    [TestMethod]
    public void RejectsInvalidBoolWithFieldPath()
    {
        var bytes = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(bytes, 2);
        var reader = new ManagedReferencePayloadReader(bytes, 0, bytes.Length);

        var exception = Assert.ThrowsException<InvalidDataException>(() =>
            reader.ReadBool32("projectile.finishOnReach"));

        StringAssert.Contains(exception.Message, "projectile.finishOnReach");
    }

    [TestMethod]
    public void CannotReadAcrossSelectedEntryBoundary()
    {
        var reader = new ManagedReferencePayloadReader(new byte[16], 4, 4);

        Assert.ThrowsException<InvalidDataException>(() => reader.ReadInt64("tooWide"));
    }
}
