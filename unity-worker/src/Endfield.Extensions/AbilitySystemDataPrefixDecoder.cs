namespace Vfs.Endfield.Extensions;

public sealed record AbilitySystemDataPrefixResult(
    IReadOnlyDictionary<string, object?> Data, int NextOffset, int RemainingLength);

/// <summary>
/// 按 1.4.4 字段顺序读取 AbilitySystemData 至 entityBlackboard；不扫描键、不选择最长候选。
/// 后缀未解释，调用方必须保留 partial 状态。managed reference 仅保存 RID，不猜测动作类型。
/// </summary>
public static class AbilitySystemDataPrefixDecoder
{
    public static AbilitySystemDataPrefixResult Decode(byte[] rawData, ManagedReferenceEntry entry)
    {
        if (entry.ClassName != "AbilitySystemData" || entry.Namespace != "Beyond.Gameplay.Core"
            || entry.AssemblyName != "Gameplay.Beyond")
            throw new InvalidDataException("目标条目不是 AbilitySystemData。");
        var r = new ManagedReferencePayloadReader(rawData, entry.DataOffset, entry.DataLength);
        var data = new Dictionary<string, object?>
        {
            ["shapeData"] = Fields(r, "shapeData", "f:detectedRadius f:detectedHeight"),
            ["modeConfig"] = new Dictionary<string, object?> { ["modes"] = List(r, "modeConfig.modes", ReadMode) },
            ["skillDataBundle"] = ReadSkillBundle(r, "skillDataBundle"),
            ["uiData"] = ReadUi(r, "uiData"),
        };
        foreach (var name in new[] { "dashBuff", "buffDuringPoiseExist", "buffDuringZeroPoise" })
            data[name] = List(r, name, ReadBuffInput);
        data["plungingAttackData"] = Fields(r, "plungingAttackData",
            "f:startDuration f:endDuration b:enableOverridePlungingAttackDownSpeed f:overridePlungingAttackDownSpeed");
        data["battleRootData"] = Fields(r, "battleRootData", "b:overrideBattleRoot i:rootMountPoint");
        Add(data, Fields(r, "abilitySystem",
            "f:poiseBrokenEndTime f:poiseKnotBreakImmobilizeTime b:playPoiseBrokenEffect b:unlockAfterOutScreen " +
            "b:overrideMarkTargetDistance f:customMarkTargetDistance b:overrideMarkTargetHeight " +
            "f:customMarkTargetHeight b:accurateMarkTargetDistance s:defaultHitEffect"));
        data["entityBlackboard"] = ReadBlackboard(r, "entityBlackboard");
        return new(data, r.Position, r.Remaining);
    }

    private static Dictionary<string, object?> ReadMode(ManagedReferencePayloadReader r, string path)
    {
        var data = Fields(r, path, "s:modeId b:defaultEnable s:modeLayer s:parentModeId b:addExtraPassiveSkill");
        data["extraPassiveSkillId"] = Strings(r, path + ".extraPassiveSkillId");
        Add(data, Fields(r, path, "b:overrideMoveSpeed f:moveSpeed b:overrideRotateRate f:rotateRate " +
            "b:isStrafing b:moveInterruptAttack b:overrideNormalAttackList"));
        data["normalAttackList"] = Strings(r, path + ".normalAttackList");
        Add(data, Fields(r, path, "b:applyAnimBool s:animBoolName b:overrideStateClip"));
        data["overrideClipMapping"] = IntStringDictionary(r, path + ".overrideClipMapping");
        Add(data, Fields(r, path, "b:overrideAnimCfg s:animCfgPath b:overrideModelKey s:modelKey " +
            "i:mountPointDefIndex b:overrideWeaponVisibilityProfile"));
        data["weaponVisibilityProfile"] = new Dictionary<string, object?>
        {
            ["slots"] = List(r, path + ".weaponVisibilityProfile.slots",
                (reader, p) => Fields(reader, p, "i:weaponIndex b:showWhenIdle b:showWhenFight")),
        };
        Add(data, Fields(r, path, "b:overrideCmdMapping"));
        data["cmdMapping"] = IntStringDictionary(r, path + ".cmdMapping");
        return data;
    }

    private static Dictionary<string, object?> ReadSkillBundle(ManagedReferencePayloadReader r, string path)
    {
        var data = new Dictionary<string, object?>();
        foreach (var name in new[] { "allNormalAttackId", "allActiveSkillId", "allPassiveSkillId",
            "normalAttackList", "enabledBreakingNormalAttacks", "enabledPassiveSkills" })
            data[name] = Strings(r, path + "." + name);
        Add(data, Fields(r, path, "s:normalSkillId s:ultimateSkillId s:plungingAttackStartId " +
            "s:plungingAttackEndId s:dodgeSkillId i:comboSkillPriorityType"));
        data["comboSkillConditions"] = List(r, path + ".comboSkillConditions", (reader, p) =>
        {
            var condition = Fields(reader, p, "i:comboSkillEvent");
            condition["comboSkillCheckAction"] = new Dictionary<string, object?>
            {
                ["actionData"] = List(reader, p + ".comboSkillCheckAction.actionData",
                    (rd, field) => rd.ReadInt64(field).ToString(System.Globalization.CultureInfo.InvariantCulture)),
            };
            var sequence = (Dictionary<string, object?>)condition["comboSkillCheckAction"]!;
            Add(sequence, Fields(reader, p + ".comboSkillCheckAction",
                "b:onlyExecuteWhenSourceIsMainChar b:onlyExecuteWhenSourceIsGuard"));
            Add(condition, Fields(reader, p, "b:comboSkillConditionImmediately"));
            return condition;
        });
        Add(data, Fields(r, path, "b:enableComboSkillBlackboard"));
        data["comboSkillBlackboard"] = ReadBlackboard(r, path + ".comboSkillBlackboard");
        Add(data, Fields(r, path, "s:comboSkillId s:comboSkillSpecialNodeName"));
        data["defaultCmdMapping"] = IntStringDictionary(r, path + ".defaultCmdMapping");
        Add(data, Fields(r, path, "s:hudPanelName"));
        var keys = Strings(r, path + ".activeSkillTypeOverrides.keys");
        var values = List(r, path + ".activeSkillTypeOverrides.values", (rd, p) => rd.ReadInt32(p));
        if (keys.Count != values.Count) throw new InvalidDataException(path + ".activeSkillTypeOverrides 数量不一致。");
        data["activeSkillTypeOverrides"] = new Dictionary<string, object?> { ["keys"] = keys, ["values"] = values };
        return data;
    }

    private static Dictionary<string, object?> ReadUi(ManagedReferencePayloadReader r, string path)
    {
        var data = Fields(r, path, "b:showBigHeadBar b:useSpecificDamageTextParam");
        data["damageTextRelated"] = Fields(r, path + ".damageTextRelated",
            "f:mainChrDmgTxtSpawnOffset.x f:mainChrDmgTxtSpawnOffset.y " +
            "f:mainChrDmgTxtMoveSpawnOffset.x f:mainChrDmgTxtMoveSpawnOffset.y " +
            "i:mainChrDmgTxtMaxMoveNum f:mainChrDmgTxtMoveSpawnWaitTime " +
            "f:guardDmgTxtSpawnOffset.x f:guardDmgTxtSpawnOffset.y f:guardDmgTxtSpawnAreaSize.x f:guardDmgTxtSpawnAreaSize.y " +
            "f:immuneTxtSpawnOffset.x f:immuneTxtSpawnOffset.y f:immuneTxtSpawnAreaSize.x f:immuneTxtSpawnAreaSize.y f:immuneTxtCooldown");
        Add(data, Fields(r, path, "b:overrideHeadBarDeltaTowardCamera f:headBarDeltaTowardCamera " +
            "f:headBar2DOffset.x f:headBar2DOffset.y b:useHeadBarGuideLine b:heightInRangeNoFollow " +
            "f:heightRange.x f:heightRange.y i:heightFollowMountPoint"));
        return data;
    }

    private static Dictionary<string, object?> ReadBuffInput(ManagedReferencePayloadReader r, string path)
    {
        var data = Fields(r, path, "s:buffId b:assignBlackboard");
        // AssignPair 与声明用 DataPair 是两个原生类型；numericValue 是 float，不是 double。
        data["assignItems"] = List(r, path + ".assignItems", (reader, p) =>
            Fields(reader, p, "s:targetKey s:inputValueKey b:useDirectValue i:directValueType f:numericValue s:stringValue"));
        return data;
    }

    private static List<Dictionary<string, object?>> ReadBlackboard(ManagedReferencePayloadReader r, string path) =>
        List(r, path, (reader, p) => new Dictionary<string, object?>
        {
            ["key"] = reader.ReadAlignedUtf8String(p + ".key"),
            ["valueDouble"] = reader.ReadDouble(p + ".valueDouble"),
            ["valueStr"] = reader.ReadAlignedUtf8String(p + ".valueStr"),
            ["isDynamic"] = reader.ReadBool32(p + ".isDynamic"),
        });

    private static Dictionary<string, object?> IntStringDictionary(ManagedReferencePayloadReader r, string path)
    {
        var keys = List(r, path + ".keys", (rd, p) => rd.ReadInt32(p));
        var values = Strings(r, path + ".values");
        if (keys.Count != values.Count) throw new InvalidDataException(path + " 键值数量不一致。");
        return new() { ["keys"] = keys, ["values"] = values };
    }

    private static List<string> Strings(ManagedReferencePayloadReader r, string path) =>
        List(r, path, (rd, p) => rd.ReadAlignedUtf8String(p));

    private static List<T> List<T>(ManagedReferencePayloadReader r, string path,
        Func<ManagedReferencePayloadReader, string, T> read)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096 || count > r.Remaining / 4)
            throw new InvalidDataException(path + $" 非法数量 {count}。");
        var result = new List<T>(count);
        for (var i = 0; i < count; i++) result.Add(read(r, $"{path}[{i}]"));
        return result;
    }

    // 这里只描述明确的序列化字段及原始类型，不包含游戏执行语义或跨字段推断。
    private static Dictionary<string, object?> Fields(ManagedReferencePayloadReader r, string path, string schema)
    {
        var result = new Dictionary<string, object?>();
        foreach (var field in schema.Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            var name = field[2..];
            var p = path + "." + name;
            result.Add(name, field[0] switch
            {
                's' => r.ReadAlignedUtf8String(p),
                'i' => r.ReadInt32(p),
                'f' => r.ReadFloat(p),
                'b' => r.ReadBool32(p),
                _ => throw new InvalidOperationException("未知声明字段类型。"),
            });
        }
        return result;
    }

    private static void Add(Dictionary<string, object?> target, Dictionary<string, object?> fields)
    {
        foreach (var pair in fields) target.Add(pair.Key, pair.Value);
    }
}
