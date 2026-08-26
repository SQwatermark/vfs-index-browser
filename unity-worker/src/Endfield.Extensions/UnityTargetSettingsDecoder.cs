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
}
