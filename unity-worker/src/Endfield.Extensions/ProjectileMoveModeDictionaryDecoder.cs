namespace Vfs.Endfield.Extensions;

public sealed record ProjectileMoveModeDictionaryDecodeResult(
    IReadOnlyDictionary<string, object?> Data,
    int NextOffset,
    int RemainingLength);

/// <summary>
/// 迁移阶段的 MoveModeData 字典解码器。当前只接受研究样本已证明的 124 word 记录，恢复
/// 元数据确认的五个前缀字段，并保留其余 115 word 原始证据。后续结构化 suffix 解码成功前，
/// 这里仍明确标记为 partial。
/// </summary>
public static class ProjectileMoveModeDictionaryDecoder
{
    private const int ObservedValueWordCount = 124;
    private const int DecodedPrefixWordCount = 9;

    public static ProjectileMoveModeDictionaryDecodeResult Decode(
        byte[] rawData,
        int offset,
        int length)
    {
        var reader = new ManagedReferencePayloadReader(rawData, offset, length);
        var keyCount = ProjectileComponentPrefixDecoder.ReadCount(
            reader,
            "projectileComponent.tail.moveModeDict.keys.count",
            64);
        var keys = new List<string>(keyCount);
        for (var index = 0; index < keyCount; index++)
        {
            keys.Add(reader.ReadAlignedAsciiString(
                $"projectileComponent.tail.moveModeDict.keys[{index}]"));
        }

        var valueCount = ProjectileComponentPrefixDecoder.ReadCount(
            reader,
            "projectileComponent.tail.moveModeDict.values.count",
            64);
        if (valueCount != keyCount)
        {
            throw new InvalidDataException(
                $"moveModeDict 的键和值数量不一致：{keyCount}/{valueCount}。");
        }

        var values = new List<Dictionary<string, object?>>(valueCount);
        for (var index = 0; index < valueCount; index++)
        {
            values.Add(ReadObservedValue(reader, keys[index], index));
        }

        var data = new Dictionary<string, object?>
        {
            ["$partial"] = true,
            ["layout"] =
                "Dictionary<string, Beyond.Gameplay.Core.ProjectileComponentData/MoveModeData>",
            ["boundaryMode"] = "observed-fixed-124-words",
            ["relativeOffset"] = offset,
            ["keyCount"] = keyCount,
            ["keys"] = keys,
            ["valueCount"] = valueCount,
            ["values"] = values,
            ["length"] = reader.Position - offset,
        };
        return new ProjectileMoveModeDictionaryDecodeResult(
            data,
            reader.Position,
            reader.Remaining);
    }

    private static Dictionary<string, object?> ReadObservedValue(
        ManagedReferencePayloadReader reader,
        string key,
        int index)
    {
        var start = reader.Position;
        var requiredBytes = ObservedValueWordCount * 4;
        if (reader.Remaining < requiredBytes)
        {
            throw new InvalidDataException(
                $"moveModeDict.values[{index}] 不足 {ObservedValueWordCount} word。");
        }

        var valueReader = new ManagedReferencePayloadReader(
            reader.RawData,
            start,
            requiredBytes);
        var traceType = ProjectileComponentPrefixDecoder.Hash32(
            valueReader.ReadInt32($"moveModeDict.values[{index}].traceType"));
        traceType["enumType"] = "Beyond.Gameplay.ProjectileTraceType";
        var traceTime = ProjectileComponentPrefixDecoder.ReadBlackboardDouble(
            valueReader,
            $"moveModeDict.values[{index}].traceTime");
        var traceUntilDistance = ProjectileComponentPrefixDecoder.ReadBlackboardDouble(
            valueReader,
            $"moveModeDict.values[{index}].traceUntilDistance");
        var moveType = ProjectileComponentPrefixDecoder.Hash32(
            valueReader.ReadInt32($"moveModeDict.values[{index}].moveType"));
        moveType["enumType"] = "Beyond.Gameplay.ProjectileMoveType";
        var parabolaDef = ProjectileComponentPrefixDecoder.Hash32(
            valueReader.ReadInt32($"moveModeDict.values[{index}].parabolaDef"));
        parabolaDef["enumType"] = "Beyond.Gameplay.ProjectileParabolaDef";
        var remainingRawWords = ProjectileComponentPrefixDecoder.ReadRawWords(
            valueReader,
            $"moveModeDict.values[{index}].remainingRawWords",
            ObservedValueWordCount - DecodedPrefixWordCount);
        valueReader.EnsureComplete();
        reader.SetPosition(valueReader.Position);

        return new Dictionary<string, object?>
        {
            ["$partial"] = true,
            ["layout"] = "Beyond.Gameplay.Core.ProjectileComponentData/MoveModeData",
            ["key"] = key,
            ["relativeOffset"] = start,
            ["wordCount"] = ObservedValueWordCount,
            ["decodedPrefixWordCount"] = DecodedPrefixWordCount,
            ["traceType"] = traceType,
            ["traceTime"] = traceTime,
            ["traceUntilDistance"] = traceUntilDistance,
            ["moveType"] = moveType,
            ["parabolaDef"] = parabolaDef,
            ["remainingRawWords"] = remainingRawWords,
        };
    }
}
