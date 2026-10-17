using System.Globalization;

namespace Vfs.Endfield.Extensions;

public sealed record AbilityEntityTemplateDecodeResult(
    string Id,
    IReadOnlyDictionary<string, object?> Template,
    IReadOnlyDictionary<string, IReadOnlyDictionary<string, object?>> Components);

/// <summary>完整读取当前已支持的能力实体配置；任何未知组件或未消费字节都会失败。</summary>
public static class AbilityEntityTemplateDecoder
{
    private static (Dictionary<long, ManagedReferenceEntry> Entries, AbilityEntityTemplatePrefixResult Prefix)
        ReadRoot(byte[] raw, string expectedId)
    {
        var registries = ManagedReferenceRegistryScanner.FindCandidates(raw).Where(candidate =>
            candidate.Entries.Any(item => item.ClassName == "AbilityEntityTemplateData"
                && item.Namespace == "Beyond.Gameplay" && item.AssemblyName == "Gameplay.Beyond")).ToArray();
        if (registries.Length != 1) throw new InvalidDataException("无法唯一定位能力实体根 registry。");
        var entries = registries[0].Entries.ToDictionary(item => item.Rid);
        var roots = entries.Values.Where(item => item.ClassName == "AbilityEntityTemplateData"
            && item.Namespace == "Beyond.Gameplay" && item.AssemblyName == "Gameplay.Beyond").ToArray();
        if (roots.Length != 1) throw new InvalidDataException("能力实体根条目不唯一。");
        var prefix = AbilityEntityTemplatePrefixDecoder.Decode(raw, roots[0]);
        if (!Equals(prefix.Data["id"], expectedId)) throw new InvalidDataException("能力实体 ID 与请求不一致。");
        return (entries, prefix);
    }

    /// <summary>在根引用的组件边界内读取能力系统前缀；不扫描其他组件中的黑板。</summary>
    public static IReadOnlyDictionary<string, object?> DecodeGameplayPrefix(byte[] raw, string expectedId)
    {
        var (entries, prefix) = ReadRoot(raw, expectedId);
        var systems = prefix.ComponentRids.Select(rid => entries.TryGetValue(rid, out var entry)
            ? entry : throw new InvalidDataException($"缺少组件 RID {rid}。"))
            .Where(entry => entry.ClassName is ("AbilitySystemData" or "AbilitySystemForEnemyPartData" or "AbilitySystemForSoulData")
                && entry.Namespace == "Beyond.Gameplay.Core" && entry.AssemblyName == "Gameplay.Beyond")
            .ToArray();
        if (systems.Length > 1) throw new InvalidDataException("能力实体的 AbilitySystemData 不唯一。");
        var ability = systems.Length == 1 ? AbilitySystemDataPrefixDecoder.Decode(raw, systems[0]) : null;
        IReadOnlyDictionary<string, object?>? tail = null;
        string? tailError = null;
        if (ability is not null)
        {
            try { tail = AbilitySystemDataTailDecoder.Decode(raw, ability.NextOffset, ability.RemainingLength, systems[0].ClassName); }
            catch (InvalidDataException error) { tailError = error.Message; }
        }
        return new Dictionary<string, object?> {
            ["id"] = expectedId, ["template"] = prefix.Data,
            ["templateRemainingLength"] = prefix.RemainingLength,
            ["abilitySystem"] = ability?.Data,
            ["abilitySystemType"] = systems.SingleOrDefault()?.ClassName,
            ["abilitySystemTail"] = tail,
            ["abilitySystemTailError"] = tailError,
            ["abilitySystemRemainingLength"] = ability?.RemainingLength,
            ["managedReferenceCount"] = entries.Count,
            ["rootRid"] = entries.Values.Single(entry => entry.ClassName == "AbilityEntityTemplateData"
                && entry.Namespace == "Beyond.Gameplay").Rid.ToString(CultureInfo.InvariantCulture),
        };
    }

    public static IReadOnlyDictionary<string, object?> DecodeSkillRegistration(byte[] raw, string expectedId)
    {
        var (entries, prefix) = ReadRoot(raw, expectedId);
        var systems = prefix.ComponentRids.Select(rid => entries.TryGetValue(rid, out var entry)
            ? entry : throw new InvalidDataException($"缺少组件 RID {rid}。"))
            .Where(entry => entry.ClassName == "AbilitySystemData"
                && entry.Namespace == "Beyond.Gameplay.Core" && entry.AssemblyName == "Gameplay.Beyond")
            .ToArray();
        if (systems.Length != 1) throw new InvalidDataException("能力实体的 AbilitySystemData 不唯一。");
        var ability = AbilitySystemDataPrefixDecoder.Decode(raw, systems[0], skillsOnly: true);
        return new Dictionary<string, object?> {
            ["id"] = expectedId, ["skillDataBundle"] = ability.Data["skillDataBundle"],
        };
    }

    public static AbilityEntityTemplateDecodeResult Decode(byte[] raw, string expectedId)
    {
        var (entries, prefix) = ReadRoot(raw, expectedId);
        var template = new Dictionary<string, object?>(prefix.Data);
        foreach (var pair in AbilityEntityTemplateTailDecoder.Decode(raw, prefix.NextOffset, prefix.RemainingLength))
            template.Add(pair.Key, pair.Value);
        var components = new Dictionary<string, IReadOnlyDictionary<string, object?>>();
        var pending = new Queue<long>(prefix.ComponentRids);
        var nav = (List<Dictionary<string, object?>>)template["navObstacleConfig"]!;
        foreach (var shape in nav)
        {
            var rid = long.Parse((string)shape["shapeRid"]!, CultureInfo.InvariantCulture);
            if (rid is not (-2 or -1 or 0)) pending.Enqueue(rid);
        }
        while (pending.TryDequeue(out var rid))
        {
            var key = rid.ToString(CultureInfo.InvariantCulture);
            if (components.ContainsKey(key)) continue;
            if (!entries.TryGetValue(rid, out var entry)) throw new InvalidDataException($"组件 RID {rid} 不存在。");
            if (entry.AssemblyName != "Gameplay.Beyond") throw new InvalidDataException("组件程序集未知。");
            var r = new ManagedReferencePayloadReader(raw, entry.DataOffset, entry.DataLength);
            Dictionary<string, object?> data;
            if (entry.Namespace == "Beyond.Gameplay.Core")
            {
                switch (entry.ClassName)
                {
                    case "AbilitySystemData":
                    case "AbilitySystemForEnemyPartData":
                    case "AbilitySystemForSoulData":
                        var ability = AbilitySystemDataPrefixDecoder.Decode(raw, entry);
                        data = new(ability.Data);
                        foreach (var pair in AbilitySystemDataTailDecoder.Decode(raw, ability.NextOffset, ability.RemainingLength, entry.ClassName)) data.Add(pair.Key, pair.Value);
                        r.SetPosition(r.End);
                        break;
                    case "AbilityEntityRootComponentData":
                        data = new();
                        foreach (var field in new[] { "mountPointData.keys", "mountPointData.values", "extraMountPointBundles", "subGameObjects", "modelParts.keys", "modelParts.values" })
                            data[field] = Empty(r, field);
                        data["snapMountPointToSurface"] = r.ReadBool32("snapMountPointToSurface");
                        data["needToSnapMountPoints"] = Empty(r, "needToSnapMountPoints");
                        foreach (var pair in Fields(r, "root", "i:maxStackingCnt b:maxStackingCntBB.useBlackboardKey i:maxStackingCntBB.value s:maxStackingCntBB.blackboardKey i:lifeType f:duration b:durationBB.useBlackboardKey f:durationBB.value s:durationBB.blackboardKey b:isEnergySource i:maxIgniteNum b:maxIgniteNumBB.useBlackboardKey i:maxIgniteNumBB.value s:maxIgniteNumBB.blackboardKey b:moveUseFrameTick i:headBarType")) data.Add(pair.Key, pair.Value);
                        break;
                    case "RotatorComponentData":
                        data = new() { ["propertyList"] = Empty(r, "propertyList") };
                        break;
                    case "CharacterMovementComponentData":
                        data = new() { ["propertyList"] = Empty(r, "propertyList") };
                        var movement = Fields(r, "movementData", "f:_moveSpeed f:_aiRotationSpeed f:_overrideStepOffset f:_overrideStepDownOffset f:_walkSpeedMultiplier f:_runSpeedMultiplier f:_sprintSpeedMultiplier f:_overrideStepOffsetForSpDash f:_overrideStepDownOffsetForSpDash b:_enableMountedJumpInSpDash");
                        // MountedJumpConfigData 是 ScriptableObject；Unity 序列化外部 PPtr，不能内联读取其字段。
                        movement["_mountedJumpConfig"] = new Dictionary<string, object?> {
                            ["fileId"] = r.ReadInt32("movementData._mountedJumpConfig.fileId"),
                            ["pathId"] = r.ReadInt64("movementData._mountedJumpConfig.pathId").ToString(CultureInfo.InvariantCulture),
                        };
                        foreach (var pair in Fields(r, "movementData", "f:_spDashFirstStageRotationSmoothTimeOverride f:_spDashSecondStageRotationSmoothTimeOverride f:_navAcceleration"))
                            movement.Add(pair.Key, pair.Value);
                        data["movementData"] = movement;
                        data["proxyShape"] = Fields(r, "proxyShape", "f:height f:radius");
                        data["overrideMoveMode"] = r.ReadInt32("overrideMoveMode");
                        var count = r.ReadInt32("abilityEntityMovementDataList.count");
                        if (count < 0 || count > 4096 || count > r.Remaining / 8) throw new InvalidDataException("移动配置引用数量非法。");
                        var movementRids = new List<string>();
                        for (var i = 0; i < count; i++)
                        {
                            var movementRid = r.ReadInt64($"abilityEntityMovementDataList[{i}]");
                            movementRids.Add(movementRid.ToString(CultureInfo.InvariantCulture));
                            if (movementRid is not (-2 or -1 or 0)) pending.Enqueue(movementRid);
                        }
                        data["abilityEntityMovementDataList"] = movementRids;
                        break;
                    case "AbilityEntityControllerData": data = new(); break;
                    default: throw new InvalidDataException($"未支持的能力实体组件：{entry.Namespace}.{entry.ClassName}");
                }
            }
            else if (entry.Namespace == "Beyond.Gameplay" && entry.ClassName == "AbilityEntityTemplateData/EaseFollowMovementData")
                data = AbilityEntityTemplateTailDecoder.ReadEaseFollow(r, "easeFollow", false);
            else if (entry.Namespace == "Beyond.Gameplay" && entry.ClassName == "AbilityEntityTemplateData/BasePositionMovementData")
                data = Fields(r, "basePosition", "i:surroundingBaseType i:rotationType i:mountPoint");
            else if (entry.Namespace == "Beyond.Gameplay.View" && entry.ClassName == "ModelComponentData")
                data = Fields(r, "model", "s:modelId f:modelScale b:enableBornFadeIn f:bornFadeInTime");
            else if (entry.Namespace == "Beyond.Gameplay" && entry.ClassName == "AbilityEntityTemplateData/BaseRotationData")
                data = Fields(r, "baseRotation", "i:baseType i:mountPoint b:followSelfRotation");
            else if (entry.Namespace == "Beyond.Gameplay.View" && entry.ClassName == "AbilityEntityAnimationComponentData")
                data = Fields(r, "animation", "s:_animCfgPath");
            else throw new InvalidDataException($"未支持的能力实体引用：{entry.Namespace}.{entry.ClassName}");
            r.EnsureComplete();
            components.Add(key, new Dictionary<string, object?> { ["type"] = entry.Namespace + "." + entry.ClassName, ["data"] = data });
        }
        return new(expectedId, template, components);
    }

    private static Dictionary<string, object?> Fields(ManagedReferencePayloadReader r, string path, string schema) => AbilitySystemDataPrefixDecoder.Fields(r, path, schema);
    private static object[] Empty(ManagedReferencePayloadReader r, string path)
    {
        if (r.ReadInt32(path + ".count") != 0) throw new InvalidDataException(path + " 非空配置尚未支持。");
        return [];
    }
}
