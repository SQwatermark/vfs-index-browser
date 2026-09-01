using System.Globalization;

namespace Vfs.Endfield.Extensions;

/// <summary>1.4.4 Unity TargetSettings 字段载荷；嵌套 managed reference 保留 RID，不猜测选择器行为。</summary>
public static class UnityTargetSettingsDecoder
{
    public static Dictionary<string, object?> Read(ManagedReferencePayloadReader r, string p) => new()
    {
        ["targetSource"] = r.ReadInt32(p + ".targetSource"),
        ["targetGroupKey"] = r.ReadAlignedUtf8String(p + ".targetGroupKey"),
        ["selectorOwner"] = r.ReadInt32(p + ".selectorOwner"),
        ["ownerContextKey"] = r.ReadAlignedUtf8String(p + ".ownerContextKey"),
        ["centerType"] = r.ReadInt32(p + ".centerType"),
        ["centerContextKey"] = r.ReadAlignedUtf8String(p + ".centerContextKey"),
        ["centerToGround"] = r.ReadBool32(p + ".centerToGround"),
        ["selectorData"] = new Dictionary<string, object?>
        {
            ["finderData"] = Rid(r, p + ".selectorData.finderData"),
            ["validatorData"] = Rids(r, p + ".selectorData.validatorData"),
            ["postProcessorData"] = Rids(r, p + ".selectorData.postProcessorData"),
        },
        ["enableAdvancedDirection"] = r.ReadBool32(p + ".enableAdvancedDirection"),
        ["advancedDirection"] = new Dictionary<string, object?>
        {
            ["directionType"] = r.ReadInt32(p + ".advancedDirection.directionType"),
            ["source"] = Rid(r, p + ".advancedDirection.source"),
            ["target"] = Rid(r, p + ".advancedDirection.target"),
            ["sourceMountPoint"] = r.ReadInt32(p + ".advancedDirection.sourceMountPoint"),
            ["targetMountPoint"] = r.ReadInt32(p + ".advancedDirection.targetMountPoint"),
            ["customSourceAndTarget"] = r.ReadBool32(p + ".advancedDirection.customSourceAndTarget"),
            ["clampToXZ"] = r.ReadBool32(p + ".advancedDirection.clampToXZ"),
            ["invertDirection"] = r.ReadBool32(p + ".advancedDirection.invertDirection"),
        },
        ["selectorDirection"] = r.ReadInt32(p + ".selectorDirection"),
        ["target"] = r.ReadInt32(p + ".target"),
        ["targetContextKey"] = r.ReadAlignedUtf8String(p + ".targetContextKey"),
    };

    private static string Rid(ManagedReferencePayloadReader r, string p) =>
        r.ReadInt64(p).ToString(CultureInfo.InvariantCulture);

    private static List<string> Rids(ManagedReferencePayloadReader r, string p)
    {
        var count = r.ReadInt32(p + ".count");
        if (count < 0 || count > 4096 || count > r.Remaining / 8)
            throw new InvalidDataException(p + $" 非法数量 {count}。");
        var result = new List<string>(count);
        for (var i = 0; i < count; i++) result.Add(Rid(r, $"{p}[{i}]"));
        return result;
    }

    /// <summary>
    /// 从已经解码的 TargetSettings 中枚举 Unity managed-reference RID。
    /// 这里只认 TargetSettings 自身声明的引用字段；负数是 Unity 的空引用哨兵，不进入依赖闭包。
    /// </summary>
    public static IEnumerable<long> EnumerateManagedReferenceRids(
        IReadOnlyDictionary<string, object?> targetSettings)
    {
        if (targetSettings.TryGetValue("selectorData", out var selectorValue)
            && selectorValue is IReadOnlyDictionary<string, object?> selector)
        {
            foreach (var rid in EnumerateRidValue(selector, "finderData")) yield return rid;
            foreach (var rid in EnumerateRidList(selector, "validatorData")) yield return rid;
            foreach (var rid in EnumerateRidList(selector, "postProcessorData")) yield return rid;
        }
        if (targetSettings.TryGetValue("advancedDirection", out var directionValue)
            && directionValue is IReadOnlyDictionary<string, object?> direction)
        {
            foreach (var rid in EnumerateRidValue(direction, "source")) yield return rid;
            foreach (var rid in EnumerateRidValue(direction, "target")) yield return rid;
        }
    }

    private static IEnumerable<long> EnumerateRidValue(
        IReadOnlyDictionary<string, object?> data, string key)
    {
        if (data.TryGetValue(key, out var value) && value is string text
            && long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var rid)
            && rid > 0)
            yield return rid;
    }

    private static IEnumerable<long> EnumerateRidList(
        IReadOnlyDictionary<string, object?> data, string key)
    {
        if (!data.TryGetValue(key, out var value) || value is not IEnumerable<string> values)
            yield break;
        foreach (var text in values)
            if (long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var rid)
                && rid > 0)
                yield return rid;
    }
}
