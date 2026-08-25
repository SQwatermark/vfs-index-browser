namespace Vfs.Endfield.Extensions;

/// <summary>
/// 从 ProjectileTemplateData 的伴随 AbilitySystemData 中恢复非空 entityBlackboard DataPair 列表。
/// AbilitySystemData 前缀仍未整体迁移，因此只接受在 payload 中唯一成立的完整 DataPair 序列。
/// </summary>
public static class ProjectileAbilitySystemBlackboardDecoder
{
    public static IReadOnlyList<Dictionary<string, object?>>? Decode(
        byte[] rawData,
        ManagedReferenceEntry entry)
    {
        ArgumentNullException.ThrowIfNull(rawData);
        if (entry.ClassName != "AbilitySystemData" ||
            entry.Namespace != "Beyond.Gameplay.Core" ||
            entry.AssemblyName != "Gameplay.Beyond")
        {
            throw new InvalidDataException("目标条目不是 AbilitySystemData。");
        }

        var matches = new List<IReadOnlyList<Dictionary<string, object?>>>();
        for (var offset = entry.DataOffset; offset <= entry.DataOffset + entry.DataLength - 8; offset += 4)
        {
            var reader = new ManagedReferencePayloadReader(rawData, entry.DataOffset, entry.DataLength);
            reader.SetPosition(offset);
            try
            {
                var count = reader.ReadInt32("abilitySystem.entityBlackboard.count");
                if (count is <= 0 or > 128)
                {
                    continue;
                }

                var pairs = new List<Dictionary<string, object?>>(count);
                for (var index = 0; index < count; index++)
                {
                    var key = reader.ReadAlignedAsciiString(
                        $"abilitySystem.entityBlackboard[{index}].key");
                    if (!key.StartsWith("EntityBB_", StringComparison.Ordinal))
                    {
                        throw new InvalidDataException("实体黑板键缺少 EntityBB_ 前缀。");
                    }
                    pairs.Add(new Dictionary<string, object?>
                    {
                        ["key"] = key,
                        ["valueDouble"] = reader.ReadDouble(
                            $"abilitySystem.entityBlackboard[{index}].valueDouble"),
                        ["valueStr"] = reader.ReadAlignedUtf8String(
                            $"abilitySystem.entityBlackboard[{index}].valueStr"),
                        ["isDynamic"] = reader.ReadBool32(
                            $"abilitySystem.entityBlackboard[{index}].isDynamic"),
                    });
                }
                matches.Add(pairs);
            }
            catch (InvalidDataException)
            {
                // 当前对齐位置不是 DataPair 列表起点。
            }
        }

        return matches.Count switch
        {
            0 => null,
            1 => matches[0],
            _ => throw new InvalidDataException(
                $"AbilitySystemData 中发现 {matches.Count} 个可能的非空 entityBlackboard 列表。"),
        };
    }
}
