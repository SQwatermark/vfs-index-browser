using System.Buffers.Binary;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ProjectileMainEffectFinishDecoderTests
{
    [TestMethod]
    public void RejectsPayloadThatMatchesNeitherKnownShape()
    {
        var bytes = new byte[8];
        BinaryPrimitives.WriteInt32LittleEndian(bytes, 99);

        Assert.ThrowsException<InvalidDataException>(() =>
            ProjectileMainEffectFinishDecoder.Decode(bytes, 0, bytes.Length));
    }
}
