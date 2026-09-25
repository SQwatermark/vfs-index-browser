using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class AbilityEntityTemplateTailDecoderTests
{
    // 各数组、字典、字符串和曲线均为空的最小序列化模板后段。
    // 不依赖游戏文件；只检查字段边界和 JSON 无法直接表示的速度上限。
    private const int EmptyTailLength = 596;
    private const int MaximumDampSpeedOffset = 268;

    [TestMethod]
    public void ReadsEmptyTailAndRejectsTruncationOrTrailingBytes()
    {
        var raw = new byte[EmptyTailLength + 4];
        var result = AbilityEntityTemplateTailDecoder.Decode(raw, 0, EmptyTailLength);
        Assert.AreEqual(false, result["hasSkill"]);
        Assert.AreEqual(false, result["useHeadBarGuideLine"]);
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilityEntityTemplateTailDecoder.Decode(raw, 0, EmptyTailLength - 4));
        Assert.ThrowsException<InvalidDataException>(() =>
            AbilityEntityTemplateTailDecoder.Decode(raw, 0, raw.Length));
    }

    [TestMethod]
    public void PreservesUnlimitedSpeedButRejectsInvalidNumbers()
    {
        var raw = new byte[EmptyTailLength];
        BitConverter.GetBytes(float.PositiveInfinity).CopyTo(raw, MaximumDampSpeedOffset);
        var result = AbilityEntityTemplateTailDecoder.Decode(raw, 0, raw.Length);
        var ease = (IReadOnlyDictionary<string, object?>)result["easeFollowConfig"]!;
        Assert.AreEqual("Infinity", ease["maxDampSpeed"]);
        foreach (var invalid in new[] { float.NaN, float.NegativeInfinity, -1f })
        {
            BitConverter.GetBytes(invalid).CopyTo(raw, MaximumDampSpeedOffset);
            Assert.ThrowsException<InvalidDataException>(() =>
                AbilityEntityTemplateTailDecoder.Decode(raw, 0, raw.Length));
        }
    }
}
