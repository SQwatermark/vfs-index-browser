using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class AbilitySystemDataTailDecoderTests
{
    [TestMethod]
    public void PreservesTrailingBuffReferenceAndChecksWholeBoundary()
    {
        // 空容器、空特效字段的独立最小布局；尾部字符串不得被当成固定长度填充跳过。
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
        writer.Write(new byte[540]);
        var text = Encoding.UTF8.GetBytes("buff_tail");
        writer.Write(text.Length);
        writer.Write(text);
        while (stream.Length % 4 != 0) writer.Write((byte)0);
        writer.Write(1);
        var bytes = stream.ToArray();
        var result = AbilitySystemDataTailDecoder.Decode(bytes, 0, bytes.Length);
        Assert.AreEqual("buff_tail", result["maxPotentialEffectBuffId"]);
        Assert.AreEqual(true, result["checkAllyBlockableMoveCollider"]);
        Assert.ThrowsException<InvalidDataException>(() => AbilitySystemDataTailDecoder.Decode(bytes, 0, bytes.Length - 4));
        Assert.ThrowsException<InvalidDataException>(() => AbilitySystemDataTailDecoder.Decode(bytes.Concat(new byte[4]).ToArray(), 0, bytes.Length + 4));
    }

    [TestMethod]
    public void RejectsUnsupportedDictionaryInsteadOfSkippingItsPayload()
    {
        var bytes = new byte[548];
        BitConverter.GetBytes(1).CopyTo(bytes, 0);
        Assert.ThrowsException<InvalidDataException>(() => AbilitySystemDataTailDecoder.Decode(bytes, 0, bytes.Length));
    }
}
