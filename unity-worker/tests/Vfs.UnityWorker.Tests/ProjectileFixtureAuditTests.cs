using System.Text.Json;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ProjectileFixtureAuditTests
{
    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void ConfiguredFixtureReportsItsAbilitySystemBlackboardBoundary()
    {
        var path = Environment.GetEnvironmentVariable(
            "VFS_WORKER_PROJECTILE_RAW_FIXTURE");
        if (string.IsNullOrWhiteSpace(path))
        {
            Assert.Inconclusive("未配置 Projectile 单样本审计路径。");
            return;
        }

        var rawData = File.ReadAllBytes(path);
        var matching = ManagedReferenceRegistryScanner.FindCandidates(rawData)
            .SelectMany(registry => registry.Entries)
            .Where(entry =>
                entry.ClassName == "AbilitySystemData" &&
                entry.Namespace == "Beyond.Gameplay.Core" &&
                entry.AssemblyName == "Gameplay.Beyond")
            .ToList();
        Assert.AreEqual(1, matching.Count, path);

        var entry = matching[0];
        var prefix = AbilitySystemDataPrefixDecoder.Decode(rawData, entry);
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            path,
            entry.DataOffset,
            entry.DataLength,
            prefix.NextOffset,
            prefix.RemainingLength,
            entityBlackboard = prefix.Data["entityBlackboard"],
            skillDataBundle = prefix.Data["skillDataBundle"],
        }));
    }

    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void EveryConfiguredFixtureReachesTheRemainingEffectTail()
    {
        var configured = Environment.GetEnvironmentVariable(
            "VFS_WORKER_PROJECTILE_RAW_FIXTURES");
        if (string.IsNullOrWhiteSpace(configured))
        {
            Assert.Inconclusive("未配置 Projectile 多样本审计路径。");
            return;
        }

        foreach (var path in configured.Split(
            Path.PathSeparator,
            StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var rawData = File.ReadAllBytes(path);
            var matching = ManagedReferenceRegistryScanner.FindCandidates(rawData)
                .SelectMany(registry => registry.Entries)
                .Where(entry =>
                    entry.ClassName == "ProjectileComponentData" &&
                    entry.Namespace == "Beyond.Gameplay.Core" &&
                    entry.AssemblyName == "Gameplay.Beyond")
                .ToList();
            Assert.AreEqual(1, matching.Count, path);

            var prefix = ProjectileComponentPrefixDecoder.Decode(rawData, matching[0]);
            var moveModes = ProjectileMoveModeDictionaryDecoder.Decode(
                rawData,
                prefix.TailOffset,
                prefix.TailLength);
            var finish = ProjectileMainEffectFinishDecoder.Decode(
                rawData,
                moveModes.NextOffset,
                moveModes.RemainingLength);
            var complete = ProjectileComponentDecoder.Decode(
                rawData,
                (string)prefix.Data["id"]!);
            Assert.IsTrue(complete.Component.ContainsKey("entityBlackboard"), path);
            var abilityPrefix = (IReadOnlyDictionary<string, object?>)complete.Component["abilitySystem"]!;
            Assert.IsTrue(abilityPrefix.ContainsKey("skillDataBundle"), path);
            Assert.AreEqual(
                JsonSerializer.Serialize(complete.Component["entityBlackboard"]),
                JsonSerializer.Serialize(abilityPrefix["entityBlackboard"]), path);

            Console.WriteLine(JsonSerializer.Serialize(new
            {
                path,
                id = prefix.Data["id"],
                componentOffset = matching[0].DataOffset,
                componentLength = matching[0].DataLength,
                prefixEnd = prefix.TailOffset,
                moveModeEnd = moveModes.NextOffset,
                finishEnd = finish.NextOffset,
                remainingEffectTailLength = finish.RemainingLength,
                complete.RemainingRawWordCount,
            }));
            Assert.IsTrue(finish.RemainingLength > 0, path);
            Assert.AreEqual(finish.RemainingLength / 4, complete.RemainingRawWordCount, path);
            var mismatch = Assert.ThrowsException<MonoBehaviourExportException>(() =>
                ProjectileComponentDecoder.Decode(rawData, "projectile_intentionally_wrong"));
            Assert.AreEqual("projectile_id_mismatch", mismatch.Code, path);
        }
    }
}
