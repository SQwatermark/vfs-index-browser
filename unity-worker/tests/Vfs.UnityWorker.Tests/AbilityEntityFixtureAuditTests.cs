using System.Text.Json;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class AbilityEntityFixtureAuditTests
{
    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void ConfiguredTemplateReportsComponentBoundaries()
    {
        var path = Environment.GetEnvironmentVariable("VFS_WORKER_ABILITY_ENTITY_RAW_FIXTURE");
        if (string.IsNullOrWhiteSpace(path))
        {
            Assert.Inconclusive("未配置能力实体原始样本路径。");
            return;
        }
        var raw = File.ReadAllBytes(path);
        var registry = ManagedReferenceRegistryScanner.FindCandidates(raw).Single(candidate =>
            candidate.Entries.Any(item => item.ClassName == "AbilityEntityTemplateData"
                && item.Namespace == "Beyond.Gameplay" && item.AssemblyName == "Gameplay.Beyond"));
        var root = registry.Entries.Single(item => item.ClassName == "AbilityEntityTemplateData");
        var template = AbilityEntityTemplatePrefixDecoder.Decode(raw, root);
        var templateTail = AbilityEntityTemplateTailDecoder.Decode(raw, template.NextOffset, template.RemainingLength);
        var complete = AbilityEntityTemplateDecoder.Decode(raw, (string)template.Data["id"]!);
        var response = WorkerProtocol.HandleRequestJson(JsonSerializer.Serialize(new
        {
            protocolVersion = WorkerProtocol.ProtocolVersion,
            requestId = "ability-entity-audit",
            operation = "decodeAbilityEntityTemplate",
            arguments = new { inputPath = path, expectedId = complete.Id },
        }));
        Assert.IsTrue(response.Ok, response.Error?.Message);
        Assert.AreEqual("ability-entity-audit", response.RequestId);
        // 协议输出也必须能序列化，包括原生的无限速度值。
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(response, WorkerProtocol.JsonOptions));
        Assert.AreEqual(complete.Id, document.RootElement.GetProperty("result").GetProperty("id").GetString());
        var components = template.ComponentRids.Select(rid => registry.Entries.Single(item => item.Rid == rid)).ToArray();
        var entry = components.Single(item => item.ClassName == "AbilitySystemData"
            && item.Namespace == "Beyond.Gameplay.Core" && item.AssemblyName == "Gameplay.Beyond");
        var prefix = AbilitySystemDataPrefixDecoder.Decode(raw, entry);
        var tail = AbilitySystemDataTailDecoder.Decode(raw, prefix.NextOffset, prefix.RemainingLength);
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            path,
            sha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(raw)),
            entries = registry.Entries,
            template,
            templateTail,
            complete,
            abilitySystemOffset = entry.DataOffset,
            abilitySystemLength = entry.DataLength,
            skillDataBundle = prefix.Data["skillDataBundle"],
            entityBlackboard = prefix.Data["entityBlackboard"],
            dashBuff = prefix.Data["dashBuff"],
            buffDuringPoiseExist = prefix.Data["buffDuringPoiseExist"],
            buffDuringZeroPoise = prefix.Data["buffDuringZeroPoise"],
            maxPotentialEffectBuffId = tail["maxPotentialEffectBuffId"],
        }));
    }
}
