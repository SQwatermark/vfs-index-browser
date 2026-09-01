using System.Globalization;
using System.Security.Cryptography;

namespace Vfs.Endfield.Extensions;

/// <summary>从 MonoBehaviour 根引用验证角色归属，再解码指定 AbilitySystem 前缀；未知载荷不冒充已支持。</summary>
public static class CharacterTemplateDecoder
{
    public static IReadOnlyDictionary<string, object?> Decode(byte[] rawData, string expectedId)
    {
        ArgumentNullException.ThrowIfNull(rawData);
        ArgumentException.ThrowIfNullOrWhiteSpace(expectedId);
        var r = new ManagedReferencePayloadReader(rawData, 0, rawData.Length);
        _ = r.ReadInt32("m_GameObject.fileID");
        _ = r.ReadInt64("m_GameObject.pathID");
        _ = r.ReadBool32("m_Enabled");
        _ = r.ReadInt32("m_Script.fileID");
        _ = r.ReadInt64("m_Script.pathID");
        var name = r.ReadAlignedUtf8String("m_Name");
        var rootRid = r.ReadInt64("data.rid");
        var registryOffset = r.Position;
        var registries = ManagedReferenceRegistryScanner.FindCandidates(rawData)
            .Where(candidate => candidate.Offset == registryOffset && candidate.Version == 2).ToArray();
        if (registries.Length != 1) throw new InvalidDataException("角色根引用后缺少唯一 version 2 registry。");
        var entries = registries[0].Entries.ToDictionary(entry => entry.Rid);
        if (!entries.TryGetValue(rootRid, out var root) || root.ClassName != "CharacterTemplateData"
            || root.Namespace != "Beyond.Gameplay" || root.AssemblyName != "Gameplay.Beyond")
            throw new InvalidDataException("MonoBehaviour.data 不指向 CharacterTemplateData。");
        r = new ManagedReferencePayloadReader(rawData, root.DataOffset, root.DataLength);
        var id = r.ReadAlignedUtf8String("id");
        if (!string.Equals(id, expectedId, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException($"角色 ID 不匹配：期望 {expectedId}，实际 {id}。");
        var rootData = new Dictionary<string, object?>
        {
            ["id"] = id, ["name"] = r.ReadAlignedUtf8String("name"),
            ["factionIndex"] = r.ReadInt32("factionIndex"),
        };
        var tags = new List<int>();
        var tagCount = ReadCount(r, "bornTag");
        for (var i = 0; i < tagCount; i++) tags.Add(r.ReadInt32($"bornTag[{i}]"));
        rootData["bornTag"] = tags;
        rootData["delayToRecycleTime"] = r.ReadFloat("delayToRecycleTime");
        rootData["delayRecyclePerformTime"] = r.ReadFloat("delayRecyclePerformTime");
        rootData["sendDieEvent"] = r.ReadBool32("sendDieEvent");
        rootData["enableBornFadeIn"] = r.ReadBool32("enableBornFadeIn");
        rootData["fadeInTime"] = r.ReadFloat("fadeInTime");
        var componentCount = ReadCount(r, "componentList");
        var components = new List<ManagedReferenceEntry>();
        for (var i = 0; i < componentCount; i++)
        {
            var rid = r.ReadInt64($"componentList[{i}]");
            if (!entries.TryGetValue(rid, out var component) || component.IsNullSentinel)
                throw new InvalidDataException($"componentList[{i}] 引用 {rid} 缺失或为空。");
            components.Add(component);
        }
        rootData["componentList"] = components.Select(Describe).ToArray();
        rootData["animConfigPath"] = r.ReadAlignedUtf8String("animConfigPath");
        rootData["bodyType"] = new Dictionary<string, object?>
        {
            ["bodyType"] = r.ReadInt32("bodyType.bodyType"),
            ["CustomName"] = r.ReadAlignedUtf8String("bodyType.CustomName"),
            ["CustomId"] = r.ReadInt32("bodyType.CustomId"),
        };
        r.EnsureComplete();
        var systems = components.Where(entry => entry.ClassName == "AbilitySystemData"
            && entry.Namespace == "Beyond.Gameplay.Core" && entry.AssemblyName == "Gameplay.Beyond").ToArray();
        if (systems.Length != 1) throw new InvalidDataException("角色必须引用唯一 AbilitySystemData。");
        var system = systems[0];
        var prefix = AbilitySystemDataPrefixDecoder.Decode(rawData, system);
        var bundle = (Dictionary<string, object?>)prefix.Data["skillDataBundle"]!;
        var conditions = (List<Dictionary<string, object?>>)bundle["comboSkillConditions"]!;
        var references = new Dictionary<string, object?>();
        var pendingReferences = new Queue<long>();
        foreach (var condition in conditions)
        {
            var sequence = (Dictionary<string, object?>)condition["comboSkillCheckAction"]!;
            foreach (var rid in (List<string>)sequence["actionData"]!)
                pendingReferences.Enqueue(long.Parse(rid, CultureInfo.InvariantCulture));
        }
        while (pendingReferences.TryDequeue(out var rid))
        {
            var ridText = rid.ToString(CultureInfo.InvariantCulture);
            if (references.ContainsKey(ridText)) continue;
            if (!entries.TryGetValue(rid, out var action) || action.IsNullSentinel)
                throw new InvalidDataException($"连携条件引用 {ridText} 缺失或为空。");
            var description = Describe(action);
            var decoded = CharacterConditionLeafDecoder.Decode(rawData, action);
            description["decodeStatus"] = decoded is null ? "raw" : "complete";
            if (decoded is not null)
            {
                description["data"] = decoded;
                foreach (var dependency in CharacterConditionLeafDecoder
                    .EnumerateManagedReferenceRids(decoded).Distinct())
                    pendingReferences.Enqueue(dependency);
            }
            description["rawBase64"] = Convert.ToBase64String(rawData, action.DataOffset, action.DataLength);
            references.Add(ridText, description);
        }
        return new Dictionary<string, object?>
        {
            ["format"] = "character-template-prefix-v1", ["decodeStatus"] = "partial",
            ["sourceSha256"] = Convert.ToHexString(SHA256.HashData(rawData)).ToLowerInvariant(),
            ["sourceByteCount"] = rawData.Length, ["monoBehaviourName"] = name,
            ["registryOffset"] = registryOffset, ["root"] = Describe(root), ["data"] = rootData,
            ["abilitySystem"] = prefix.Data, ["abilitySystemEntry"] = Describe(system),
            ["abilitySystemTailOffset"] = prefix.NextOffset,
            ["abilitySystemTailBase64"] = Convert.ToBase64String(rawData, prefix.NextOffset, prefix.RemainingLength),
            ["conditionReferences"] = references,
        };
    }

    private static Dictionary<string, object?> Describe(ManagedReferenceEntry entry) => new()
    {
        ["rid"] = entry.Rid.ToString(CultureInfo.InvariantCulture), ["class"] = entry.ClassName,
        ["namespace"] = entry.Namespace, ["assembly"] = entry.AssemblyName,
        ["offset"] = entry.DataOffset, ["length"] = entry.DataLength,
    };

    private static int ReadCount(ManagedReferencePayloadReader r, string path)
    {
        var count = r.ReadInt32(path + ".count");
        if (count < 0 || count > 4096 || count > r.Remaining / 4)
            throw new InvalidDataException($"{path} 非法数量 {count}。");
        return count;
    }
}
