using System.Globalization;

namespace Vfs.Endfield.Extensions;

public sealed record AbilityEntityTemplatePrefixResult(
    IReadOnlyDictionary<string, object?> Data,
    IReadOnlyList<long> ComponentRids,
    int NextOffset,
    int RemainingLength);

/// <summary>
/// 在根条目的字节边界内读取能力实体身份、组件引用及寿命参数。
/// 保留未读取后段，不把此结果当成完整模板；组件必须按这些 RID 查找，不能按类型全局猜选。
/// </summary>
public static class AbilityEntityTemplatePrefixDecoder
{
    public static AbilityEntityTemplatePrefixResult Decode(byte[] raw, ManagedReferenceEntry entry)
    {
        if (entry.ClassName != "AbilityEntityTemplateData" || entry.Namespace != "Beyond.Gameplay"
            || entry.AssemblyName != "Gameplay.Beyond")
            throw new InvalidDataException("目标条目不是 AbilityEntityTemplateData。");
        var reader = new ManagedReferencePayloadReader(raw, entry.DataOffset, entry.DataLength);
        var data = AbilitySystemDataPrefixDecoder.Fields(reader, "template", "s:id s:name i:faction");
        var tags = ReadCount(reader, "bornTag", 4);
        data["bornTag"] = Enumerable.Range(0, tags).Select(i => reader.ReadInt32($"bornTag[{i}]")).ToArray();
        foreach (var pair in AbilitySystemDataPrefixDecoder.Fields(reader, "template",
                     "f:delayToRecycleTime f:delayRecyclePerformTime b:sendDieEvent b:enableBornFadeIn f:fadeInTime"))
            data.Add(pair.Key, pair.Value);
        var count = ReadCount(reader, "componentList", 8);
        var rids = new List<long>(count);
        for (var i = 0; i < count; i++)
        {
            var rid = reader.ReadInt64($"componentList[{i}]");
            if (rid is 0 or -1 or -2 || rids.Contains(rid))
                throw new InvalidDataException($"componentList[{i}] 包含空、保留或重复 RID。");
            rids.Add(rid);
        }
        data["componentList"] = rids.Select(rid => rid.ToString(CultureInfo.InvariantCulture)).ToArray();
        data["maxStackingCnt"] = reader.ReadInt32("maxStackingCnt");
        data["maxStackingCntBB"] = AbilitySystemDataPrefixDecoder.Fields(reader, "maxStackingCntBB",
            "b:useBlackboardKey i:value s:blackboardKey");
        data["lifeType"] = reader.ReadInt32("lifeType");
        data["duration"] = reader.ReadFloat("duration");
        data["durationBB"] = AbilitySystemDataPrefixDecoder.Fields(reader, "durationBB",
            "b:useBlackboardKey f:value s:blackboardKey");
        data["maxDurationForServer"] = reader.ReadFloat("maxDurationForServer");
        return new(data, rids, reader.Position, reader.Remaining);
    }

    private static int ReadCount(ManagedReferencePayloadReader reader, string path, int itemSize)
    {
        var count = reader.ReadInt32(path + ".count");
        if (count < 0 || count > 4096 || count > reader.Remaining / itemSize)
            throw new InvalidDataException($"{path} 数量超出条目边界：{count}。");
        return count;
    }
}
