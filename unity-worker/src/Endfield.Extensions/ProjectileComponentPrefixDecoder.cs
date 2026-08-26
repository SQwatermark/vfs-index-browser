namespace Vfs.Endfield.Extensions;

public sealed record ProjectileComponentPrefixDecodeResult(
    IReadOnlyDictionary<string, object?> Data,
    int TailOffset,
    int TailLength);

/// <summary>
/// 只解码已经由 IL2CPP 字段顺序和真实样本共同确认的 ProjectileComponentData 前缀。
/// moveModeDict 及其后的可变尾部尚未迁移，因此本类型不会宣称得到完整组件。
/// </summary>
public static class ProjectileComponentPrefixDecoder
{
    public static ProjectileComponentPrefixDecodeResult Decode(
        byte[] rawData,
        ManagedReferenceEntry entry)
    {
        ArgumentNullException.ThrowIfNull(rawData);
        if (entry.ClassName != "ProjectileComponentData" ||
            entry.Namespace != "Beyond.Gameplay.Core" ||
            entry.AssemblyName != "Gameplay.Beyond")
        {
            throw new InvalidDataException("目标条目不是 ProjectileComponentData。");
        }

        var reader = new ManagedReferencePayloadReader(
            rawData,
            entry.DataOffset,
            entry.DataLength);
        var data = new Dictionary<string, object?>
        {
            ["$decoded"] = true,
            ["$partial"] = true,
            ["layout"] = "Beyond.Gameplay.Core.ProjectileComponentData",
            ["offset"] = entry.DataOffset,
            ["length"] = entry.DataLength,
            ["id"] = reader.ReadAlignedAsciiString("projectileComponent.id"),
            ["finishDuration"] = ReadBlackboardDouble(reader, "projectileComponent.finishDuration"),
            ["finishDistance"] = ReadBlackboardDouble(reader, "projectileComponent.finishDistance"),
            ["finishOnReach"] = reader.ReadBool32("projectileComponent.finishOnReach"),
            ["hitOnReach"] = reader.ReadBool32("projectileComponent.hitOnReach"),
            ["colliderShapeData"] = ReadShapeData(reader, "projectileComponent.colliderShapeData"),
            ["blockLayerDef"] = DecodeProjectileBlockLayerDef(
                reader.ReadInt32("projectileComponent.blockLayerDef")),
            ["blockLayer"] = Hash32(reader.ReadInt32("projectileComponent.blockLayer")),
            ["targetFilter"] = ReadTargetFilter(reader, "projectileComponent.targetFilter"),
            ["ignoreImmuneLevel"] = ReadSparseEnum(
                reader,
                "projectileComponent.ignoreImmuneLevel",
                (0, "Default"),
                (1, "IgnoreDashImmune")),
            ["maxHitCount"] = ReadBlackboardInt(reader, "projectileComponent.maxHitCount"),
            ["allowHitSameTarget"] = reader.ReadBool32("projectileComponent.allowHitSameTarget"),
            ["hitIntervalPerTarget"] = reader.ReadFloat("projectileComponent.hitIntervalPerTarget"),
            ["collisionDetectTiming"] = Hash32(
                reader.ReadInt32("projectileComponent.collisionDetectTiming")),
            ["hitAndBlockDetectDelayTime"] = ReadBlackboardDouble(
                reader,
                "projectileComponent.hitAndBlockDetectDelayTime"),
            ["hitAndBlockDetectDelayDistance"] = ReadBlackboardDouble(
                reader,
                "projectileComponent.hitAndBlockDetectDelayDistance"),
            ["keepMoveOnReach"] = reader.ReadBool32("projectileComponent.keepMoveOnReach"),
            ["canTraceTargetAfterReach"] = reader.ReadBool32(
                "projectileComponent.canTraceTargetAfterReach"),
            ["presetPointKeys"] = ReadStringList(
                reader,
                "projectileComponent.presetPointKeys",
                64),
            ["useSegmentMove"] = reader.ReadBool32("projectileComponent.useSegmentMove"),
            ["moveSegments"] = ReadObjectList(
                reader,
                "projectileComponent.moveSegments",
                64,
                ReadMoveSegment),
        };

        return new ProjectileComponentPrefixDecodeResult(
            data,
            reader.Position,
            reader.Remaining);
    }

    private static Dictionary<string, object?> ReadShapeData(
        ManagedReferencePayloadReader reader,
        string fieldPath) =>
        new()
        {
            ["$decoded"] = true,
            ["layout"] = "Beyond.Gameplay.Core.ProjectileComponentData/ShapeData",
            ["shapeType"] = ReadSparseEnum(
                reader,
                $"{fieldPath}.shapeType",
                (0, "None"),
                (1, "Sphere"),
                (2, "Box"),
                (3, "Ring")),
            ["radius"] = ReadBlackboardDouble(reader, $"{fieldPath}.radius"),
            ["center"] = ReadBlackboardVector3(reader, $"{fieldPath}.center"),
            ["extent"] = ReadBlackboardVector3(reader, $"{fieldPath}.extent"),
            ["initOuterRadius"] = ReadBlackboardDouble(reader, $"{fieldPath}.initOuterRadius"),
            ["initInnerRadius"] = ReadBlackboardDouble(reader, $"{fieldPath}.initInnerRadius"),
            ["outerRadiusIncreaseSpeed"] = ReadBlackboardDouble(
                reader,
                $"{fieldPath}.outerRadiusIncreaseSpeed"),
            ["innerRadiusIncreaseSpeed"] = ReadBlackboardDouble(
                reader,
                $"{fieldPath}.innerRadiusIncreaseSpeed"),
            ["height"] = ReadBlackboardDouble(reader, $"{fieldPath}.height"),
            ["isSector"] = reader.ReadBool32($"{fieldPath}.isSector"),
            ["sectorDirection"] = ReadSparseEnum(
                reader,
                $"{fieldPath}.sectorDirection",
                (0, "SelfForward"),
                (1, "StartPosToTarget"),
                (2, "SelfToTarget")),
            ["sectorAngle"] = ReadBlackboardDouble(reader, $"{fieldPath}.sectorAngle"),
        };

    private static Dictionary<string, object?> ReadTargetFilter(
        ManagedReferencePayloadReader reader,
        string fieldPath) =>
        new()
        {
            ["$decoded"] = true,
            ["layout"] = "Beyond.Gameplay.Core.TargetFilter",
            ["checkAlive"] = reader.ReadBool32($"{fieldPath}.checkAlive"),
            ["autoSetTargetFaction"] = reader.ReadBool32($"{fieldPath}.autoSetTargetFaction"),
            ["factionTarget"] = Hash32(reader.ReadInt32($"{fieldPath}.factionTarget")),
            ["targetFactionType"] = Hash32(reader.ReadInt32($"{fieldPath}.targetFactionType")),
            ["filterObjectType"] = reader.ReadBool32($"{fieldPath}.filterObjectType"),
            ["objectType"] = Hash32(reader.ReadInt32($"{fieldPath}.objectType")),
            ["filterSlot"] = reader.ReadBool32($"{fieldPath}.filterSlot"),
            ["slotIndex"] = reader.ReadInt32($"{fieldPath}.slotIndex"),
            ["filterGameplayTag"] = reader.ReadBool32($"{fieldPath}.filterGameplayTag"),
            ["tagQuery"] = ReadGameplayTagQuery(reader, $"{fieldPath}.tagQuery"),
        };

    private static Dictionary<string, object?> ReadMoveSegment(
        ManagedReferencePayloadReader reader) =>
        new()
        {
            ["$decoded"] = true,
            ["layout"] = "Beyond.Gameplay.Core.ProjectileComponentData/MoveSegment",
            ["startPointKey"] = reader.ReadAlignedAsciiString(
                "projectileComponent.moveSegments.startPointKey"),
            ["moveModeId"] = reader.ReadAlignedAsciiString(
                "projectileComponent.moveSegments.moveModeId"),
            ["endPointKey"] = reader.ReadAlignedAsciiString(
                "projectileComponent.moveSegments.endPointKey"),
            ["earlyNextByDuration"] = reader.ReadBool32(
                "projectileComponent.moveSegments.earlyNextByDuration"),
            ["segmentDuration"] = ReadBlackboardDouble(
                reader,
                "projectileComponent.moveSegments.segmentDuration"),
            ["skipHitAndBlockDetection"] = reader.ReadBool32(
                "projectileComponent.moveSegments.skipHitAndBlockDetection"),
            ["speedLerpTime"] = ReadBlackboardDouble(
                reader,
                "projectileComponent.moveSegments.speedLerpTime"),
        };

    private static Dictionary<string, object?> ReadBlackboardVector3(
        ManagedReferencePayloadReader reader,
        string fieldPath)
    {
        var x = ReadBlackboardDouble(reader, $"{fieldPath}.x");
        var y = ReadBlackboardDouble(reader, $"{fieldPath}.y");
        var z = ReadBlackboardDouble(reader, $"{fieldPath}.z");
        return new Dictionary<string, object?>
        {
            ["layout"] = "Beyond.Blackboard.BlackboardVector3",
            ["x"] = x,
            ["y"] = y,
            ["z"] = z,
            ["valueCandidate"] = new Dictionary<string, object?>
            {
                ["x"] = x["valueFloatCandidate"],
                ["y"] = y["valueFloatCandidate"],
                ["z"] = z["valueFloatCandidate"],
            },
        };
    }

    internal static Dictionary<string, object?> ReadBlackboardDouble(
        ManagedReferencePayloadReader reader,
        string fieldPath)
    {
        var start = reader.Position;
        var local = new ManagedReferencePayloadReader(
            reader.RawData,
            reader.Position,
            reader.Remaining);
        try
        {
            var useBlackboardKey = local.ReadBool32($"{fieldPath}.useBlackboardKey");
            var value = local.ReadFloat($"{fieldPath}.value");
            var blackboardKey = local.ReadAlignedAsciiString($"{fieldPath}.blackboardKey");
            reader.SetPosition(local.Position);
            return new Dictionary<string, object?>
            {
                ["layout"] = "Beyond.Blackboard.BlackboardDouble",
                ["serializationShape"] = "bool-float-key",
                ["useBlackboardKey"] = useBlackboardKey,
                ["value"] = value,
                ["blackboardKey"] = blackboardKey,
                ["valueFloatCandidate"] = value,
            };
        }
        catch (InvalidDataException)
        {
            reader.SetPosition(start);
            var rawWords = ReadRawWords(reader, $"{fieldPath}.rawWords", 3);
            var value = BitConverter.Int32BitsToSingle((int)rawWords[1]["value"]!);
            return new Dictionary<string, object?>
            {
                ["layout"] = "Beyond.Blackboard.BlackboardDouble",
                ["serializationShape"] = "raw-three-word",
                ["rawWords"] = rawWords,
                ["valueFloatCandidate"] = value,
            };
        }
    }

    private static Dictionary<string, object?> ReadBlackboardInt(
        ManagedReferencePayloadReader reader,
        string fieldPath)
    {
        var rawWords = ReadRawWords(reader, $"{fieldPath}.rawWords", 3);
        return new Dictionary<string, object?>
        {
            ["layout"] = "Beyond.Blackboard.BlackboardInt",
            ["rawWords"] = rawWords,
            ["valueIntCandidate"] = rawWords[1]["value"],
        };
    }

    internal static Dictionary<string, object?> ReadGameplayTagQuery(
        ManagedReferencePayloadReader reader,
        string fieldPath)
    {
        var queryType = reader.ReadInt32($"{fieldPath}.queryType");
        var names = new[] { "HasAny", "HasAll", "ExceptAny", "ExceptAll" };
        if (queryType < 0 || queryType >= names.Length)
        {
            throw new InvalidDataException($"{fieldPath}.queryType 包含非法枚举值 {queryType}。");
        }
        return new Dictionary<string, object?>
        {
            ["queryType"] = new Dictionary<string, object?>
            {
                ["value"] = queryType,
                ["name"] = names[queryType],
            },
            ["tags"] = ReadGameplayTags(reader, $"{fieldPath}.tags", 32),
        };
    }

    private static List<Dictionary<string, object?>> ReadGameplayTags(
        ManagedReferencePayloadReader reader,
        string fieldPath,
        int maximumCount)
    {
        var count = ReadCount(reader, $"{fieldPath}.count", maximumCount);
        var items = new List<Dictionary<string, object?>>(count);
        for (var index = 0; index < count; index++)
        {
            items.Add(new Dictionary<string, object?>
            {
                ["tagId"] = Hash32(reader.ReadInt32($"{fieldPath}[{index}].tagId")),
            });
        }
        return items;
    }

    private static List<T> ReadObjectList<T>(
        ManagedReferencePayloadReader reader,
        string fieldPath,
        int maximumCount,
        Func<ManagedReferencePayloadReader, T> readItem)
    {
        var count = ReadCount(reader, $"{fieldPath}.count", maximumCount);
        var items = new List<T>(count);
        for (var index = 0; index < count; index++)
        {
            items.Add(readItem(reader));
        }
        return items;
    }

    private static List<string> ReadStringList(
        ManagedReferencePayloadReader reader,
        string fieldPath,
        int maximumCount)
    {
        var count = ReadCount(reader, $"{fieldPath}.count", maximumCount);
        var items = new List<string>(count);
        for (var index = 0; index < count; index++)
        {
            items.Add(reader.ReadAlignedAsciiString($"{fieldPath}[{index}]"));
        }
        return items;
    }

    internal static int ReadCount(
        ManagedReferencePayloadReader reader,
        string fieldPath,
        int maximumCount)
    {
        var count = reader.ReadInt32(fieldPath);
        if (count < 0 || count > maximumCount)
        {
            throw new InvalidDataException($"{fieldPath} 包含非法数量 {count}。");
        }
        return count;
    }

    internal static List<Dictionary<string, object?>> ReadRawWords(
        ManagedReferencePayloadReader reader,
        string fieldPath,
        int count)
    {
        var words = new List<Dictionary<string, object?>>(count);
        for (var index = 0; index < count; index++)
        {
            words.Add(Hash32(reader.ReadInt32($"{fieldPath}[{index}]")));
        }
        return words;
    }

    private static Dictionary<string, object?> ReadSparseEnum(
        ManagedReferencePayloadReader reader,
        string fieldPath,
        params (int Value, string Name)[] names)
    {
        var value = reader.ReadInt32(fieldPath);
        var matched = names.FirstOrDefault(item => item.Value == value);
        var result = Hash32(value);
        if (matched.Name is not null)
        {
            result["name"] = matched.Name;
        }
        return result;
    }

    /// <summary>
    /// 1.4.4 _CalculateTouchingLayer 先对序列化值加一：结果 0 读取自定义层，1 清空阻挡层，
    /// 2 使用墙体/地面层。因此原生值是 -1/0/1，而不是元数据声明顺序的 0/1/2。
    /// </summary>
    internal static Dictionary<string, object?> DecodeProjectileBlockLayerDef(int value)
    {
        var result = Hash32(value);
        result["name"] = value switch
        {
            -1 => "Custom",
            0 => "Nothing",
            1 => "WallAndGround",
            _ => null,
        };
        if (result["name"] is null) result.Remove("name");
        return result;
    }

    internal static Dictionary<string, object?> Hash32(int value) =>
        new()
        {
            ["value"] = value,
            ["hex"] = $"0x{unchecked((uint)value):x8}",
        };
}
