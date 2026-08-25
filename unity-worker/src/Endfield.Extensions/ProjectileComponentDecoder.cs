namespace Vfs.Endfield.Extensions;

public sealed record ProjectileComponentDecodeResult(
    IReadOnlyDictionary<string, object?> Component,
    int ComponentOffset,
    int ComponentLength,
    int StructuredPrefixEnd,
    int RemainingRawWordCount);

/// <summary>
/// 组合已经独立验证的 Projectile 解码阶段，并把尚未证明语义的尾部完整保留为 Raw words。
/// 只有所有字节都被结构化阶段或 Raw 证据阶段消费后才返回结果，但结果仍标记 partial。
/// </summary>
public static class ProjectileComponentDecoder
{
    public static ProjectileComponentDecodeResult Decode(
        byte[] rawData,
        string expectedProjectileId)
    {
        ArgumentNullException.ThrowIfNull(rawData);
        if (string.IsNullOrWhiteSpace(expectedProjectileId))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "expectedProjectileId 不能为空。");
        }

        var entries = ManagedReferenceRegistryScanner.FindCandidates(rawData)
            .SelectMany(registry => registry.Entries)
            .Where(entry =>
                entry.ClassName == "ProjectileComponentData" &&
                entry.Namespace == "Beyond.Gameplay.Core" &&
                entry.AssemblyName == "Gameplay.Beyond")
            .ToList();
        if (entries.Count != 1)
        {
            throw new MonoBehaviourExportException(
                "projectile_component_not_unique",
                $"Raw 中应有且仅有一个 ProjectileComponentData，实际为 {entries.Count} 个。");
        }

        var entry = entries[0];
        var prefix = ProjectileComponentPrefixDecoder.Decode(rawData, entry);
        var actualId = prefix.Data["id"] as string;
        if (!string.Equals(actualId, expectedProjectileId, StringComparison.Ordinal))
        {
            throw new MonoBehaviourExportException(
                "projectile_id_mismatch",
                $"Projectile ID 不匹配：期望 {expectedProjectileId}，实际 {actualId}。");
        }

        var moveModes = ProjectileMoveModeDictionaryDecoder.Decode(
            rawData,
            prefix.TailOffset,
            prefix.TailLength);
        var mainEffectFinish = ProjectileMainEffectFinishDecoder.Decode(
            rawData,
            moveModes.NextOffset,
            moveModes.RemainingLength);
        if (mainEffectFinish.RemainingLength % 4 != 0)
        {
            throw new MonoBehaviourExportException(
                "projectile_tail_unaligned",
                $"Projectile 未解析尾部不是 4 字节对齐：{mainEffectFinish.RemainingLength} 字节。");
        }

        var remainingReader = new ManagedReferencePayloadReader(
            rawData,
            mainEffectFinish.NextOffset,
            mainEffectFinish.RemainingLength);
        var remainingWordCount = mainEffectFinish.RemainingLength / 4;
        var remainingWords = ProjectileComponentPrefixDecoder.ReadRawWords(
            remainingReader,
            "projectileComponent.tail.remainingRawWords",
            remainingWordCount);
        remainingReader.EnsureComplete();

        var component = new Dictionary<string, object?>(prefix.Data)
        {
            ["decodeStatus"] = "partial",
            ["tail"] = new Dictionary<string, object?>
            {
                ["$partial"] = true,
                ["relativeOffset"] = prefix.TailOffset,
                ["moveModeDict"] = moveModes.Data,
                ["mainEffectFinishTypeSerialized"] = mainEffectFinish.FinishTypeSerialized,
                ["mainEffectFinishType"] = mainEffectFinish.FinishType,
                ["mainEffectFinishDistance"] = mainEffectFinish.FinishDistance,
                ["remainingRawOffset"] = mainEffectFinish.NextOffset,
                ["remainingRawWordCount"] = remainingWordCount,
                ["remainingRawWords"] = remainingWords,
                ["length"] = entry.DataOffset + entry.DataLength - prefix.TailOffset,
            },
        };
        return new ProjectileComponentDecodeResult(
            component,
            entry.DataOffset,
            entry.DataLength,
            mainEffectFinish.NextOffset,
            remainingWordCount);
    }
}
