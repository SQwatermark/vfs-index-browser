namespace Vfs.Endfield.Extensions;

/// <summary>
/// 读取 entityBlackboard 后的字段。当前支持空间/镜头字典为空的模板；不跳过未知长度数据。
/// 字段顺序由留存类型声明、当前 AKEDATA EffectActionCfg 和原始资源共同核对。
/// </summary>
public static class AbilitySystemDataTailDecoder
{
    public static IReadOnlyDictionary<string, object?> Decode(byte[] raw, int offset, int length)
    {
        var r = new ManagedReferencePayloadReader(raw, offset, length);
        var data = new Dictionary<string, object?>();
        data["bakedMeshPoints"] = EmptyDictionary(r, "bakedMeshPoints");
        data["bakedMeshPointBonePathList"] = List(r, "bakedMeshPointBonePathList", (reader, p) => reader.ReadAlignedUtf8String(p));
        data["extraShapesData"] = EmptyDictionary(r, "extraShapesData");
        data["skillCameraConfig"] = EmptyDictionary(r, "skillCameraConfig");
        data["overrideDeadEffect"] = r.ReadBool32("overrideDeadEffect");
        data["deadEffect"] = Effect(r, "deadEffect");
        data["deadEffects"] = List(r, "deadEffects", Effect);
        Add(data, Fields(r, "abilitySystem", "f:effectScale b:isPlayHitFlash s:hitFlashAsset i:healthType"));
        var keys = List(r, "preloadAbilityEntities.keys", (reader, p) => reader.ReadAlignedUtf8String(p));
        var values = List(r, "preloadAbilityEntities.values", (reader, p) => reader.ReadInt32(p));
        if (keys.Count != values.Count) throw new InvalidDataException("preloadAbilityEntities 键值数量不一致。");
        data["preloadAbilityEntities"] = new Dictionary<string, object?> { ["keys"] = keys, ["values"] = values };
        Add(data, Fields(r, "abilitySystem", "s:maxPotentialEffectBuffId b:checkAllyBlockableMoveCollider"));
        r.EnsureComplete();
        return data;
    }

    private static Dictionary<string, object?> Effect(ManagedReferencePayloadReader r, string path)
    {
        // forceGuardEffect 和 centerOffset 存在于运行时类型，但不在当前资源序列化字段中。
        var d = Fields(r, path, "i:fxType s:effectName b:guardEffect b:isCenterChangeLod b:useScaleBB");
        d["scale"] = Vector(r, path + ".scale");
        d["scaleBB"] = BlackboardVector(r, path + ".scaleBB");
        d["useLengthBB"] = r.ReadBool32(path + ".useLengthBB");
        d["lengthBB"] = BlackboardNumber(r, path + ".lengthBB");
        d["useDurationScaleBB"] = r.ReadBool32(path + ".useDurationScaleBB");
        d["durationScaleBB"] = BlackboardNumber(r, path + ".durationScaleBB");
        Add(d, Fields(r, path, "b:releaseByAction b:ignoreOwnerTimeScale f:interruptTime b:terrainPrefab"));
        if (r.ReadInt32(path + ".effectPosData.count") != 0)
            throw new InvalidDataException(path + ".effectPosData 非空形态尚未解码。");
        d["effectPosData"] = Array.Empty<object>();
        Add(d, Fields(r, path,
            "b:isShowInDialog b:isLimitEffectCount i:limitCount f:protectTime f:limitTime s:limitKey " +
            "b:assetOnlyAffectModelRoot b:isUltimateShow b:visibleWithEntity i:visibleWithEntityType " +
            "b:ignoreEntityDither i:moveType b:useCameraViewportAnchor"));
        d["cameraViewportPosition"] = Fields(r, path + ".cameraViewportPosition", "f:x f:y");
        Add(d, Fields(r, path,
            "f:cameraAnchorDistance i:cameraScreenSizeScaleMode f:cameraReferenceFov f:cameraReferenceAspect " +
            "i:positionRef b:grounded b:followGrounded f:followGroundedMaxDistance b:lerpToTargetTrans " +
            "f:lerpDuration b:followHideTarget b:visibleWhenHideTarget i:slotIndex b:useWeaponMountPoint " +
            "i:mountPoint b:useAccurateMp b:isClothMountPoint i:weaponIndex i:weaponMountPoint " +
            "b:showHideWithWeapon i:offsetDir b:offsetDirRevert b:usePositionOffsetBB"));
        d["positionOffset"] = Vector(r, path + ".positionOffset");
        d["positionOffsetBB"] = BlackboardVector(r, path + ".positionOffsetBB");
        Add(d, Fields(r, path,
            "b:useTargetRotation b:scaleWithTargetSize f:fxSize i:unpackPosDelayFrame " +
            "b:unpackFollowTargetOnRelease i:rotType i:rotRef i:directionRef b:rotUseWeaponMountPoint " +
            "i:rotMountPoint i:rotWeaponIndex i:rotWeaponMountPoint b:revertDir b:useSelfRotationBB"));
        d["selfRotation"] = Vector(r, path + ".selfRotation");
        d["selfRotationBB"] = BlackboardVector(r, path + ".selfRotationBB");
        Add(d, Fields(r, path,
            "b:lockYRotation i:unpackRotDelayFrame b:unpackFollowTargetRotOnRelease s:weaponVfxKey " +
            "i:weaponVfxIndex b:weaponVfxPersistent i:alertType b:animateAlert f:alertAnimateDuration " +
            "b:isAlertAnimateReverse f:angle f:hollow i:modifyType f:value"));
        return d;
    }

    private static Dictionary<string, object?> EmptyDictionary(ManagedReferencePayloadReader r, string path)
    {
        if (r.ReadInt32(path + ".keys.count") != 0 || r.ReadInt32(path + ".values.count") != 0)
            throw new InvalidDataException(path + " 非空形态尚未解码。");
        return new() { ["keys"] = Array.Empty<object>(), ["values"] = Array.Empty<object>() };
    }

    private static Dictionary<string, object?> Vector(ManagedReferencePayloadReader r, string path) => Fields(r, path, "f:x f:y f:z");
    private static Dictionary<string, object?> BlackboardNumber(ManagedReferencePayloadReader r, string path) =>
        Fields(r, path, "b:useBlackboardKey f:value s:blackboardKey");
    private static Dictionary<string, object?> BlackboardVector(ManagedReferencePayloadReader r, string path) => new()
    {
        ["x"] = BlackboardNumber(r, path + ".x"),
        ["y"] = BlackboardNumber(r, path + ".y"),
        ["z"] = BlackboardNumber(r, path + ".z"),
    };
    private static Dictionary<string, object?> Fields(ManagedReferencePayloadReader r, string path, string schema) =>
        AbilitySystemDataPrefixDecoder.Fields(r, path, schema);
    private static void Add(Dictionary<string, object?> target, Dictionary<string, object?> fields)
    {
        foreach (var pair in fields) target.Add(pair.Key, pair.Value);
    }
    private static List<T> List<T>(ManagedReferencePayloadReader r, string path, Func<ManagedReferencePayloadReader, string, T> read)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096) throw new InvalidDataException(path + " 非法数量。");
        var result = new List<T>(count);
        for (var i = 0; i < count; i++) result.Add(read(r, $"{path}[{i}]"));
        return result;
    }
}
