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
        var mainCharacter = entry.ClassName == "CheckMainCharacterCondition/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var contextBuff = entry.ClassName == "CheckBuffIdInContext/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var tagMatch = entry.ClassName == "CheckTagMatch/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var notNext = entry.ClassName == "NotNextCheckAction/Data"
            && entry.Namespace == "Beyond.Gameplay.Core";
        var hp = entry.ClassName == "CheckHp/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var buffStack = entry.ClassName == "CheckBuffStackNum/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var createBuff = entry.ClassName == "CreateBuffAction/Data"
            && entry.Namespace == "Beyond.Gameplay.Core";
        var storeBuffCount = entry.ClassName == "StoreBuffCount/Data"
            && entry.Namespace == "Beyond.Gameplay.Core";
        var returnFalse = entry.ClassName == "ReturnFalseAction/Data"
            && entry.Namespace == "Beyond.Gameplay.Core";
        var ifElse = entry.ClassName == "IfElseAction/IfElseActionData"
            && entry.Namespace == "Beyond.Gameplay.Core";
        if (!spell && !compare && !objectType && !tagStack && !debug && !damageDecorate
            && !targetsEqual && !advancedStack && !advancedBuffId && !modifyBlackboard && !physical
            && !mainCharacter && !contextBuff && !tagMatch && !notNext && !hp && !buffStack
            && !createBuff && !storeBuffCount && !returnFalse && !ifElse) return null;
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
        else if (mainCharacter)
        {
            data["checkTarget"] = UnityTargetSettingsDecoder.Read(r, "checkTarget");
        }
        else if (contextBuff)
        {
            data["checkType"] = r.ReadInt32("checkType");
            data["buffIdList"] = ReadBuffIds(r, "buffIdList");
            data["query"] = ProjectileComponentPrefixDecoder.ReadGameplayTagQuery(r, "query");
            data["blackboardKey"] = r.ReadAlignedUtf8String("blackboardKey");
        }
        else if (tagMatch)
        {
            data["checkTarget"] = UnityTargetSettingsDecoder.Read(r, "checkTarget");
            data["query"] = ProjectileComponentPrefixDecoder.ReadGameplayTagQuery(r, "query");
        }
        else if (notNext)
        {
            // Data 只有公共动作头；不制造虚构字段。
        }
        else if (hp)
        {
            data["hpOwner"] = UnityTargetSettingsDecoder.Read(r, "hpOwner");
            data["compare"] = r.ReadInt32("compare");
            data["isRatio"] = r.ReadBool32("isRatio");
            data["value"] = ReadNumber(r, "value");
        }
        else if (buffStack)
        {
            data["checkTarget"] = UnityTargetSettingsDecoder.Read(r, "checkTarget");
            data["buffId"] = new Dictionary<string, object?>
            {
                ["buffId"] = r.ReadAlignedUtf8String("buffId.buffId"),
            };
            data["compareType"] = r.ReadInt32("compareType");
            data["value"] = ReadNumber(r, "value");
        }
        else if (createBuff)
        {
            data["buffs"] = ReadCreateBuffInputs(r, "buffs");
            data["count"] = ReadNumber(r, "count");
            data["targetSettings"] = UnityTargetSettingsDecoder.Read(r, "targetSettings");
            data["buffSource"] = r.ReadInt32("buffSource");
            data["contextKey"] = r.ReadAlignedUtf8String("contextKey");
            data["autoFinishByAction"] = r.ReadBool32("autoFinishByAction");
            data["inheritSkillIdList"] = ReadStrings(r, "inheritSkillIdList");
            data["finishWithNextSkillIfNotInherited"] = r.ReadBool32(
                "finishWithNextSkillIfNotInherited");
            data["asChildBuff"] = r.ReadBool32("asChildBuff");
            data["inheritSourceSkillCastId"] = r.ReadBool32("inheritSourceSkillCastId");
            data["inheritSourceSkillCastInfo"] = r.ReadBool32("inheritSourceSkillCastInfo");
            data["isExtra"] = r.ReadBool32("isExtra");
            data["passTargetGroupsToBuff"] = r.ReadBool32("passTargetGroupsToBuff");
            data["overrideBuffIconDuration"] = r.ReadBool32("overrideBuffIconDuration");
            data["buffIconDurationSource"] = new Dictionary<string, object?>
            {
                ["durationSourceType"] = r.ReadInt32("buffIconDurationSource.durationSourceType"),
                ["timedMarkerId"] = r.ReadAlignedUtf8String(
                    "buffIconDurationSource.timedMarkerId"),
            };
        }
        else if (storeBuffCount)
        {
            data["useCurrentBuff"] = r.ReadBool32("useCurrentBuff");
            data["buffOwners"] = UnityTargetSettingsDecoder.Read(r, "buffOwners");
            data["buffId"] = r.ReadAlignedUtf8String("buffId");
            data["blackboardKey"] = r.ReadAlignedUtf8String("blackboardKey");
        }
        else if (ifElse)
        {
            data["conditionAction"] = ReadActionSequenceReferences(r, "conditionAction");
            data["succeedActions"] = ReadActionSequenceReferences(r, "succeedActions");
            data["failActions"] = ReadActionSequenceReferences(r, "failActions");
            data["alwaysNext"] = r.ReadBool32("alwaysNext");
        }
        else if (returnFalse)
        {
            // Data 只有公共动作头；返回值语义由下游公共动作实现。
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
        if (data.TryGetValue("actionData", out var actionData)
            && actionData is IEnumerable<string> references)
            foreach (var reference in references)
                if (long.TryParse(reference, out var rid) && rid > 0)
                    yield return rid;
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

    private static List<Dictionary<string, object?>> ReadBuffIds(
        ManagedReferencePayloadReader r, string path)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096)
            throw new InvalidDataException($"{path} 非法数量 {count}。");
        var values = new List<Dictionary<string, object?>>(count);
        for (var i = 0; i < count; i++)
            values.Add(new Dictionary<string, object?>
            {
                ["buffId"] = r.ReadAlignedUtf8String($"{path}[{i}].buffId"),
            });
        return values;
    }

    private static List<Dictionary<string, object?>> ReadCreateBuffInputs(
        ManagedReferencePayloadReader r, string path)
    {
        var count = ReadCount(r, path);
        var values = new List<Dictionary<string, object?>>(count);
        for (var i = 0; i < count; i++)
        {
            var itemPath = $"{path}[{i}]";
            values.Add(new Dictionary<string, object?>
            {
                ["buffId"] = r.ReadAlignedUtf8String(itemPath + ".buffId"),
                ["assignBlackboard"] = r.ReadBool32(itemPath + ".assignBlackboard"),
                ["assignItems"] = ReadBlackboardAssignItems(r, itemPath + ".assignItems"),
                ["readIdFromBlackboard"] = r.ReadBool32(itemPath + ".readIdFromBlackboard"),
                ["buffIdKey"] = r.ReadAlignedUtf8String(itemPath + ".buffIdKey"),
            });
        }
        return values;
    }

    private static List<Dictionary<string, object?>> ReadBlackboardAssignItems(
        ManagedReferencePayloadReader r, string path)
    {
        var count = ReadCount(r, path);
        var values = new List<Dictionary<string, object?>>(count);
        for (var i = 0; i < count; i++)
        {
            var itemPath = $"{path}[{i}]";
            values.Add(new Dictionary<string, object?>
            {
                ["targetKey"] = r.ReadAlignedUtf8String(itemPath + ".targetKey"),
                ["inputValueKey"] = r.ReadAlignedUtf8String(itemPath + ".inputValueKey"),
                ["useDirectValue"] = r.ReadBool32(itemPath + ".useDirectValue"),
                ["directValueType"] = r.ReadInt32(itemPath + ".directValueType"),
                ["numericValue"] = r.ReadFloat(itemPath + ".numericValue"),
                ["stringValue"] = r.ReadAlignedUtf8String(itemPath + ".stringValue"),
            });
        }
        return values;
    }

    private static List<string> ReadStrings(ManagedReferencePayloadReader r, string path)
    {
        var count = ReadCount(r, path);
        var values = new List<string>(count);
        for (var i = 0; i < count; i++)
            values.Add(r.ReadAlignedUtf8String($"{path}[{i}]"));
        return values;
    }

    private static int ReadCount(ManagedReferencePayloadReader r, string path)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096)
            throw new InvalidDataException($"{path} 非法数量 {count}。");
        return count;
    }

    private static Dictionary<string, object?> ReadActionSequenceReferences(
        ManagedReferencePayloadReader r, string path)
    {
        var count = ReadCount(r, path + ".actionData");
        var actions = new List<string>(count);
        for (var i = 0; i < count; i++)
            actions.Add(r.ReadInt64($"{path}.actionData[{i}]").ToString());
        return new Dictionary<string, object?>
        {
            ["actionData"] = actions,
            ["onlyExecuteWhenSourceIsMainChar"] = r.ReadBool32(
                path + ".onlyExecuteWhenSourceIsMainChar"),
            ["onlyExecuteWhenSourceIsGuard"] = r.ReadBool32(
                path + ".onlyExecuteWhenSourceIsGuard"),
        };
    }

    private static Dictionary<string, object?> ReadNumber(ManagedReferencePayloadReader r, string path) => new()
    {
        ["useKey"] = r.ReadBool32(path + ".useKey"),
        ["value"] = r.ReadFloat(path + ".value"),
        ["key"] = r.ReadAlignedUtf8String(path + ".key"),
    };
}
