namespace Vfs.Endfield.Extensions;

/// <summary>Verified Unity-serialized leaves only; unknown types remain raw, malformed known types fail.</summary>
public static class CharacterConditionLeafDecoder
{
    public static IReadOnlyDictionary<string, object?>? Decode(byte[] raw, ManagedReferenceEntry entry)
    {
        if (entry.AssemblyName != "Gameplay.Beyond") return null;
        var spell = entry.ClassName == "CheckSpellInflictionType/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var compare = entry.ClassName == "CompareFloat/Data" && entry.Namespace == "Beyond.Gameplay.Core";
        var objectType = entry.ClassName == "CheckObjectTypeMatch/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var tagStack = entry.ClassName == "CheckBuffStackNumByTag/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var debug = entry.ClassName == "DebugPrintAction/Data" && entry.Namespace == "Beyond.Gameplay.Core";
        var damageDecorate = entry.ClassName == "CheckDamageDecorateMask/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var targetsEqual = entry.ClassName == "CheckTargetsEqual/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var advancedStack = entry.ClassName == "CheckBuffStackNumAdvanced/Data"
            && entry.Namespace == "Beyond.Gameplay.Core";
        var advancedBuffId = entry.ClassName == "CheckBuffIdInContextAdvanced/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var modifyBlackboard = entry.ClassName == "ModifyDynamicBlackboard/Data"
            && entry.Namespace == "Beyond.Gameplay.Core";
        var physical = entry.ClassName == "CheckPhysicalInflictionType/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        if (!spell && !compare && !objectType && !tagStack && !debug && !damageDecorate
            && !targetsEqual && !advancedStack && !advancedBuffId && !modifyBlackboard && !physical) return null;
        var r = new ManagedReferencePayloadReader(raw, entry.DataOffset, entry.DataLength);
        var data = new Dictionary<string, object?>
        {
            ["isEnable"] = r.ReadBool32("isEnable"),
            ["priorityLevel"] = r.ReadInt32("priorityLevel"),
            ["priorityOffset"] = r.ReadInt32("priorityOffset"),
            ["serverActionIndex"] = r.ReadInt32("serverActionIndex"),
        };
        if (spell || physical)
        {
            data["mask"] = r.ReadInt32("mask");
            data["savedKey"] = r.ReadAlignedUtf8String("savedKey");
        }
        else if (compare)
        {
            // Unity declaration order, NOT MemoryPack member order. Serialized value is float32.
            data["valueA"] = ReadNumber(r, "valueA");
            data["compare"] = r.ReadInt32("compare");
            data["valueB"] = ReadNumber(r, "valueB");
        }
        else if (objectType)
        {
            data["target"] = UnityTargetSettingsDecoder.Read(r, "target");
            data["objectTypeMask"] = r.ReadInt32("objectTypeMask");
        }
        else if (tagStack)
        {
            data["checkTarget"] = UnityTargetSettingsDecoder.Read(r, "checkTarget");
            data["tagQuery"] = ProjectileComponentPrefixDecoder.ReadGameplayTagQuery(r, "tagQuery");
            data["buffStackNumType"] = r.ReadInt32("buffStackNumType");
            data["compareType"] = r.ReadInt32("compareType");
            data["value"] = ReadNumber(r, "value");
        }
        else if (damageDecorate)
        {
            data["checkType"] = r.ReadInt32("checkType");
            data["mask"] = r.ReadInt64("mask");
        }
        else if (targetsEqual)
        {
            data["firstTargetSettings"] = UnityTargetSettingsDecoder.Read(r, "firstTargetSettings");
            data["secondTargetSettings"] = UnityTargetSettingsDecoder.Read(r, "secondTargetSettings");
        }
        else if (advancedStack)
        {
            data["checkTarget"] = UnityTargetSettingsDecoder.Read(r, "checkTarget");
            data["buffSettings"] = ReadBuffFindSettings(r, "buffSettings");
            data["buffStackNumType"] = r.ReadInt32("buffStackNumType");
            data["compareType"] = r.ReadInt32("compareType");
            data["value"] = ReadNumber(r, "value");
            data["limitSkillCastId"] = r.ReadBool32("limitSkillCastId");
        }
        else if (advancedBuffId)
        {
            data["checkType"] = r.ReadInt32("checkType");
            data["buffIdList"] = ReadBlackboardStrings(r, "buffIdList");
            data["query"] = ProjectileComponentPrefixDecoder.ReadGameplayTagQuery(r, "query");
            data["blackboardKey"] = r.ReadAlignedUtf8String("blackboardKey");
        }
        else if (modifyBlackboard)
        {
            data["key"] = r.ReadAlignedUtf8String("key");
            data["operation"] = r.ReadInt32("operation");
            data["directValue"] = r.ReadBool32("directValue");
            data["value"] = ReadNumber(r, "value");
            data["calculationTarget"] = UnityTargetSettingsDecoder.Read(r, "calculationTarget");
            data["calculateType"] = r.ReadInt32("calculateType");
        }
        else
        {
            data["logType"] = r.ReadInt32("logType");
            data["target"] = UnityTargetSettingsDecoder.Read(r, "target");
            data["color"] = new Dictionary<string, object?>
            {
                ["r"] = r.ReadFloat("color.r"), ["g"] = r.ReadFloat("color.g"),
                ["b"] = r.ReadFloat("color.b"), ["a"] = r.ReadFloat("color.a"),
            };
            data["bbKey"] = r.ReadAlignedUtf8String("bbKey");
            data["identifier"] = r.ReadAlignedUtf8String("identifier");
        }
        r.EnsureComplete();
        return data;
    }

    /// <summary>
    /// 枚举已解码条件叶子直接引用的 selector/finder/validator 等 managed reference。
    /// 递归遍历只是为了覆盖叶子内不同命名的 TargetSettings 字段，不把普通字符串猜成 RID。
    /// </summary>
    public static IEnumerable<long> EnumerateManagedReferenceRids(object? value)
    {
        if (value is not IReadOnlyDictionary<string, object?> data) yield break;
        if (data.ContainsKey("selectorData") && data.ContainsKey("advancedDirection"))
            foreach (var rid in UnityTargetSettingsDecoder.EnumerateManagedReferenceRids(data))
                yield return rid;
        foreach (var child in data.Values)
            foreach (var rid in EnumerateManagedReferenceRids(child))
                yield return rid;
    }

    private static Dictionary<string, object?> ReadBuffFindSettings(
        ManagedReferencePayloadReader r, string path)
    {
        var checkType = r.ReadInt32(path + ".checkType");
        var count = r.ReadInt32(path + ".buffIdList.count");
        if (count < 0 || count > 4096)
            throw new InvalidDataException($"{path}.buffIdList 非法数量 {count}。");
        var ids = new List<string>(count);
        for (var i = 0; i < count; i++)
            ids.Add(r.ReadAlignedUtf8String($"{path}.buffIdList[{i}]"));
        return new Dictionary<string, object?>
        {
            ["checkType"] = checkType,
            ["buffIdList"] = ids,
            ["tagQuery"] = ProjectileComponentPrefixDecoder.ReadGameplayTagQuery(
                r, path + ".tagQuery"),
        };
    }

    private static List<Dictionary<string, object?>> ReadBlackboardStrings(
        ManagedReferencePayloadReader r, string path)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096)
            throw new InvalidDataException($"{path} 非法数量 {count}。");
        var values = new List<Dictionary<string, object?>>(count);
        for (var i = 0; i < count; i++)
        {
            values.Add(new Dictionary<string, object?>
            {
                ["useKey"] = r.ReadBool32($"{path}[{i}].useKey"),
                ["value"] = r.ReadAlignedUtf8String($"{path}[{i}].value"),
                ["key"] = r.ReadAlignedUtf8String($"{path}[{i}].key"),
            });
        }
        return values;
    }

    private static Dictionary<string, object?> ReadNumber(ManagedReferencePayloadReader r, string path) => new()
    {
        ["useKey"] = r.ReadBool32(path + ".useKey"),
        ["value"] = r.ReadFloat(path + ".value"),
        ["key"] = r.ReadAlignedUtf8String(path + ".key"),
    };
}
