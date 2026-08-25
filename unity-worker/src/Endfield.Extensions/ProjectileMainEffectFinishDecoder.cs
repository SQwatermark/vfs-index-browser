namespace Vfs.Endfield.Extensions;

public sealed record ProjectileMainEffectFinishDecodeResult(
    bool FinishTypeSerialized,
    IReadOnlyDictionary<string, object?> FinishType,
    IReadOnlyDictionary<string, object?> FinishDistance,
    int NextOffset,
    int RemainingLength);

/// <summary>
/// 解码 moveModeDict 后的主特效结束条件。游戏样本同时存在“枚举 + 距离”和仅距离两种
/// 序列化形态，因此按强约束依次探测，失败时不移动调用方边界。
/// </summary>
public static class ProjectileMainEffectFinishDecoder
{
    public static ProjectileMainEffectFinishDecodeResult Decode(
        byte[] rawData,
        int offset,
        int length)
    {
        if (TryDecodeWithType(rawData, offset, length, out var result))
        {
            return result;
        }
        if (TryDecodeDistanceOnly(rawData, offset, length, out result))
        {
            return result;
        }
        throw new InvalidDataException(
            "主特效结束条件既不符合枚举加距离形态，也不符合仅距离形态。");
    }

    private static bool TryDecodeWithType(
        byte[] rawData,
        int offset,
        int length,
        out ProjectileMainEffectFinishDecodeResult result)
    {
        var reader = new ManagedReferencePayloadReader(rawData, offset, length);
        try
        {
            var value = reader.ReadInt32("projectileComponent.tail.mainEffectFinishType");
            if (value is < 0 or > 2)
            {
                throw new InvalidDataException($"非法 ProjectileMainEffectFinishType：{value}。");
            }
            var distance = ProjectileComponentPrefixDecoder.ReadBlackboardDouble(
                reader,
                "projectileComponent.tail.mainEffectFinishDistance");
            result = new ProjectileMainEffectFinishDecodeResult(
                true,
                BuildFinishType(value),
                distance,
                reader.Position,
                reader.Remaining);
            return true;
        }
        catch (InvalidDataException)
        {
            result = null!;
            return false;
        }
    }

    private static bool TryDecodeDistanceOnly(
        byte[] rawData,
        int offset,
        int length,
        out ProjectileMainEffectFinishDecodeResult result)
    {
        var reader = new ManagedReferencePayloadReader(rawData, offset, length);
        try
        {
            var distance = ProjectileComponentPrefixDecoder.ReadBlackboardDouble(
                reader,
                "projectileComponent.tail.mainEffectFinishDistance");
            result = new ProjectileMainEffectFinishDecodeResult(
                false,
                new Dictionary<string, object?>
                {
                    ["$omitted"] = true,
                    ["enumType"] = "Beyond.Gameplay.ProjectileMainEffectFinishType",
                },
                distance,
                reader.Position,
                reader.Remaining);
            return true;
        }
        catch (InvalidDataException)
        {
            result = null!;
            return false;
        }
    }

    private static Dictionary<string, object?> BuildFinishType(int value)
    {
        var result = ProjectileComponentPrefixDecoder.Hash32(value);
        result["enumType"] = "Beyond.Gameplay.ProjectileMainEffectFinishType";
        result["name"] = value switch
        {
            0 => "Default",
            1 => "ByTargetPosition",
            2 => "ByMaxDistance",
            _ => throw new InvalidDataException($"非法 ProjectileMainEffectFinishType：{value}。"),
        };
        return result;
    }
}
