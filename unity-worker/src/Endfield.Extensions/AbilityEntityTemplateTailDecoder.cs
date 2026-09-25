namespace Vfs.Endfield.Extensions;

/// <summary>逐字段读取能力实体模板的剩余配置。未知非空容器拒绝解析，不跳过固定字节数。</summary>
public static class AbilityEntityTemplateTailDecoder
{
    public static IReadOnlyDictionary<string, object?> Decode(byte[] raw, int offset, int length)
    {
        var r = new ManagedReferencePayloadReader(raw, offset, length);
        var data = Fields(r, "template", "b:canMove f:moveHeight f:moveRadius i:moveType b:checkAllyBlockableMoveCollider b:useFrameTick");
        var surrounding = Fields(r, "surroundingConfig", "i:surroundingBase i:mountPoint i:rotationType f:centerOffset.x f:centerOffset.y f:centerOffset.z f:normalVector.x f:normalVector.y f:normalVector.z f:radius");
        surrounding["radiusBB"] = Scalar(r, "surroundingConfig.radiusBB");
        surrounding["angleSpeed"] = r.ReadFloat("surroundingConfig.angleSpeed");
        surrounding["angleSpeedBB"] = Scalar(r, "surroundingConfig.angleSpeedBB");
        Add(surrounding, Fields(r, "surroundingConfig", "b:rotationClockwise i:initAngleType f:initAngle"));
        surrounding["initAngleBB"] = Scalar(r, "surroundingConfig.initAngleBB");
        data["surroundingConfig"] = surrounding;
        data["followMountPointConfig"] = Fields(r, "followMountPointConfig", "i:baseType i:mountPoint b:followSelfRotation");
        var follow = Fields(r, "gamePlayAbilityEntityFollowMainCharData", "f:_followOffset.x f:_followOffset.y f:_followOffset.z");
        follow["_normalFollowOffsets"] = ReadList(r, "gamePlayAbilityEntityFollowMainCharData._normalFollowOffsets", p => Fields(r, p, "f:x f:y f:z"));
        Add(follow, Fields(r, "gamePlayAbilityEntityFollowMainCharData", "f:_smoothTimeHorizontal f:_smoothTimeVertical f:_maxSpeed"));
        follow["_worldYCompensation"] = Curve(r, "gamePlayAbilityEntityFollowMainCharData._worldYCompensation");
        follow["_worldXZCompensation"] = Curve(r, "gamePlayAbilityEntityFollowMainCharData._worldXZCompensation");
        follow["_smoothTimeVelocityCompensation"] = r.ReadFloat("gamePlayAbilityEntityFollowMainCharData._smoothTimeVelocityCompensation");
        data["gamePlayAbilityEntityFollowMainCharData"] = follow;
        data["easeFollowConfig"] = ReadEaseFollow(r, "easeFollowConfig", true);
        Add(data, Fields(r, "template", "b:posToGround b:hasSkill"));
        data["skillDataBundle"] = AbilitySystemDataPrefixDecoder.ReadSkillBundle(r, "skillDataBundle");
        Add(data, Fields(r, "template", "b:requiresCastSkillConfirm b:hasModel s:modelKey"));
        data["mountPointDef"] = EmptyDictionary(r, "mountPointDef");
        data["modelParts"] = ReadList(r, "modelParts", p =>
        {
            var part = Fields(r, p, "i:modelPartEnum");
            part["modelPartNames"] = ReadList(r, p + ".modelPartNames", name => r.ReadAlignedUtf8String(name));
            return part;
        });
        data["hasNavObstacle"] = r.ReadBool32("hasNavObstacle");
        data["navObstacleConfig"] = ReadList(r, "navObstacleConfig", p =>
        {
            var obstacle = Fields(r, p, "i:obstacleTypeForConfig s:key b:defaultEnable i:positionRef f:centerOffset.x f:centerOffset.y f:centerOffset.z i:rotationRef f:rotationOffset.x f:rotationOffset.y f:rotationOffset.z s:areaName");
            obstacle["shapeRid"] = r.ReadInt64(p + ".shapeData").ToString(System.Globalization.CultureInfo.InvariantCulture);
            return obstacle;
        });
        Add(data, Fields(r, "template", "b:canBeSelect f:detectedHeight f:detectedRadius b:physical"));
        data["physicalData"] = Fields(r, "physicalData", "f:mass f:drag f:angularDrag i:collisionDetectionMode");
        Add(data, Fields(r, "template", "b:hasBattlePhysicalComponents b:hasAirborneComponent b:hasKnockDownComponent b:hasPullComponent b:hasMovementComponent b:hasAnimation s:animationPath b:hasInteractiveAction f:maxPickUpTime"));
        data["interactiveActions"] = EmptyDictionary(r, "interactiveActions");
        Add(data, Fields(r, "template", "b:isEnergySource i:maxIgniteNum"));
        data["maxIgniteNumBB"] = Fields(r, "maxIgniteNumBB", "b:useBlackboardKey i:value s:blackboardKey");
        Add(data, Fields(r, "template", "b:isUltimateShow b:hasGameplayComponents b:hasTyphoeaArcheryLockingComponent b:hasSuperArmor"));
        data["initialSuperArmor"] = Fields(r, "initialSuperArmor", "b:useBlackboardKey i:value s:blackboardKey b:useCustomValue");
        Add(data, Fields(r, "template", "i:healthType i:headBarType b:overrideHeadBarDeltaTowardCamera f:headBarDeltaTowardCamera f:headBar2DOffset.x f:headBar2DOffset.y b:useHeadBarGuideLine"));
        r.EnsureComplete();
        return data;
    }

    private static Dictionary<string, object?> Fields(ManagedReferencePayloadReader r, string path, string schema) =>
        AbilitySystemDataPrefixDecoder.Fields(r, path, schema);
    internal static Dictionary<string, object?> ReadEaseFollow(ManagedReferencePayloadReader r, string path, bool hasLimitFlag)
    {
        var ease = Fields(r, path, "i:targetType i:mountPoint i:forwardBasis");
        ease["offset"] = new Dictionary<string, object?>
        {
            ["x"] = Scalar(r, path + ".offset.x"), ["y"] = Scalar(r, path + ".offset.y"), ["z"] = Scalar(r, path + ".offset.z"),
        };
        ease["curve"] = Curve(r, path + ".curve");
        Add(ease, Fields(r, path, "f:smoothTimeXY f:smoothTimeForward"));
        if (hasLimitFlag) ease["hasMaxDampSpeed"] = r.ReadBool32(path + ".hasMaxDampSpeed");
        ease["maxDampSpeed"] = ReadMaximumSpeed(r, path + ".maxDampSpeed");
        Add(ease, Fields(r, path, "b:enableRootYawLag f:rootYawLagSmoothTime f:maxRootYawLagAngle b:enableHorizontalSpeedLag"));
        ease["horizontalSpeedLag"] = Curve(r, path + ".horizontalSpeedLag");
        Add(ease, Fields(r, path, "f:speedLagSmoothTime i:rotationType"));
        return ease;
    }
    private static Dictionary<string, object?> Scalar(ManagedReferencePayloadReader r, string path) =>
        Fields(r, path, "b:useBlackboardKey f:value s:blackboardKey");
    // Unity 以正无穷表示不限制阻尼速度；JSON 没有对应数值，保留明确字符串而非改成零。
    private static object ReadMaximumSpeed(ManagedReferencePayloadReader r, string path)
    {
        var value = BitConverter.Int32BitsToSingle(r.ReadInt32(path));
        if (float.IsPositiveInfinity(value)) return "Infinity";
        if (!float.IsFinite(value) || value < 0) throw new InvalidDataException(path + " 非法速度上限。");
        return value;
    }
    private static void Add(Dictionary<string, object?> target, Dictionary<string, object?> values)
    {
        foreach (var pair in values) target.Add(pair.Key, pair.Value);
    }
    private static List<T> ReadList<T>(ManagedReferencePayloadReader r, string path, Func<string, T> read)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096 || count > r.Remaining / 4)
            throw new InvalidDataException($"{path} 非法数量 {count}。");
        var items = new List<T>(count);
        for (var i = 0; i < count; i++) items.Add(read($"{path}[{i}]"));
        return items;
    }
    private static Dictionary<string, object?> Curve(ManagedReferencePayloadReader r, string path)
    {
        var keys = ReadList(r, path + ".keys", p => Fields(r, p,
            "f:time f:value f:inSlope f:outSlope i:weightedMode f:inWeight f:outWeight"));
        var curve = Fields(r, path, "i:preInfinity i:postInfinity i:rotationOrder");
        curve["keys"] = keys;
        return curve;
    }
    private static object[] EmptyList(ManagedReferencePayloadReader r, string path)
    {
        var count = r.ReadInt32(path + ".count");
        if (count != 0)
            throw new InvalidDataException($"{path} 非空内容尚未支持：offset={r.Position - 4}, count={count}。");
        return [];
    }
    private static Dictionary<string, object?> EmptyDictionary(ManagedReferencePayloadReader r, string path) =>
        new() { ["keys"] = EmptyList(r, path + ".keys"), ["values"] = EmptyList(r, path + ".values") };
}
