using System.Buffers.Binary;
using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ManagedReferenceRegistryScannerTests
{
    [TestMethod]
    public void FindsRegistryAndPreservesPayloadBoundaries()
    {
        var bytes = new List<byte>();
        WriteInt32(bytes, 2);
        WriteInt32(bytes, 2);
        WriteHeader(bytes, 17, "RootData", "Beyond.Gameplay.Core", "Gameplay.Beyond");
        bytes.AddRange([0xaa, 0xbb, 0xcc, 0xdd]);
        WriteHeader(bytes, 23, "ProjectileComponentData", "Beyond.Gameplay.Core", "Gameplay.Beyond");
        bytes.AddRange([0x01, 0x02, 0x03, 0x04, 0x05]);

        var candidates = ManagedReferenceRegistryScanner.FindCandidates([.. bytes]);

        Assert.AreEqual(1, candidates.Count);
        var registry = candidates[0];
        Assert.AreEqual(0, registry.Offset);
        Assert.AreEqual(2, registry.Version);
        Assert.AreEqual(2, registry.Entries.Count);
        Assert.AreEqual("RootData", registry.Entries[0].ClassName);
        Assert.AreEqual(4, registry.Entries[0].DataLength);
        Assert.AreEqual("ProjectileComponentData", registry.Entries[1].ClassName);
        Assert.AreEqual(5, registry.Entries[1].DataLength);
    }

    [TestMethod]
    public void RejectsIntegerPairsThatAreNotRegistries()
    {
        var bytes = new byte[64];
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(0, 4), 2);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(4, 4), 3);

        Assert.AreEqual(0, ManagedReferenceRegistryScanner.FindCandidates(bytes).Count);
    }

    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void TangtangProjectileFixtureHasExpectedRegistryBoundary()
    {
        var fixture = Environment.GetEnvironmentVariable("VFS_WORKER_PROJECTILE_RAW_FIXTURE");
        if (string.IsNullOrWhiteSpace(fixture))
        {
            Assert.Inconclusive("未设置本机真实样本路径。此测试由产品化证据验证命令启用。");
            return;
        }

        var candidates = ManagedReferenceRegistryScanner.FindCandidates(File.ReadAllBytes(fixture));
        var matching = candidates.Where(candidate => candidate.Entries.Any(entry =>
            entry.ClassName == "ProjectileComponentData" &&
            entry.Namespace == "Beyond.Gameplay.Core" &&
            entry.AssemblyName == "Gameplay.Beyond")).ToList();

        Assert.AreEqual(
            1,
            matching.Count,
            string.Join(
                "; ",
                candidates.Select(candidate =>
                    $"offset={candidate.Offset}, entries={string.Join(',', candidate.Entries.Select(entry => entry.ClassName))}")));
        Assert.AreEqual(84, matching[0].Offset);
        Assert.AreEqual(2, matching[0].Version);
        Assert.AreEqual(4, matching[0].Entries.Count);
        var projectile = matching[0].Entries.Single(entry =>
            entry.ClassName == "ProjectileComponentData");
        Assert.AreEqual(1480, projectile.HeaderOffset);
        Assert.AreEqual(1560, projectile.DataOffset);
        Assert.AreEqual(3428, projectile.DataLength);

        var prefix = ProjectileComponentPrefixDecoder.Decode(
            File.ReadAllBytes(fixture),
            projectile);
        Assert.AreEqual("projectile_chr_0027_tangtang_attack3", prefix.Data["id"]);
        Assert.AreEqual(true, prefix.Data["finishOnReach"]);
        Assert.AreEqual(false, prefix.Data["hitOnReach"]);
        Assert.AreEqual(false, prefix.Data["allowHitSameTarget"]);
        Assert.AreEqual(-1f, prefix.Data["hitIntervalPerTarget"]);
        Assert.AreEqual(2032, prefix.TailOffset);
        Assert.AreEqual(2956, prefix.TailLength);

        var finishDuration = (Dictionary<string, object?>)prefix.Data["finishDuration"]!;
        Assert.AreEqual(2f, finishDuration["valueFloatCandidate"]);
        var segments = (List<Dictionary<string, object?>>)prefix.Data["moveSegments"]!;
        Assert.AreEqual(1, segments.Count);
        Assert.AreEqual("LaunchPoint", segments[0]["startPointKey"]);
        Assert.AreEqual("Default", segments[0]["moveModeId"]);
        Assert.AreEqual("TargetPoint", segments[0]["endPointKey"]);

        var moveModes = ProjectileMoveModeDictionaryDecoder.Decode(
            File.ReadAllBytes(fixture),
            prefix.TailOffset,
            prefix.TailLength);
        Assert.AreEqual(2548, moveModes.NextOffset);
        Assert.AreEqual(2440, moveModes.RemainingLength);
        Assert.AreEqual(516, moveModes.Data["length"]);
        var moveModeValues = (List<Dictionary<string, object?>>)moveModes.Data["values"]!;
        Assert.AreEqual(1, moveModeValues.Count);
        Assert.AreEqual("Default", moveModeValues[0]["key"]);
        Assert.AreEqual(124, moveModeValues[0]["wordCount"]);
        Assert.AreEqual(9, moveModeValues[0]["decodedPrefixWordCount"]);

        var mainEffectFinish = ProjectileMainEffectFinishDecoder.Decode(
            File.ReadAllBytes(fixture),
            moveModes.NextOffset,
            moveModes.RemainingLength);
        Assert.IsTrue(mainEffectFinish.FinishTypeSerialized);
        Assert.AreEqual(2564, mainEffectFinish.NextOffset);
        Assert.AreEqual(2424, mainEffectFinish.RemainingLength);
        Assert.AreEqual("Default", mainEffectFinish.FinishType["name"]);
        Assert.AreEqual(40f, mainEffectFinish.FinishDistance["valueFloatCandidate"]);
    }

    private static void WriteHeader(
        List<byte> bytes,
        long rid,
        string className,
        string namespaceName,
        string assemblyName)
    {
        WriteInt64(bytes, rid);
        WriteAlignedString(bytes, className);
        WriteAlignedString(bytes, namespaceName);
        WriteAlignedString(bytes, assemblyName);
    }

    private static void WriteAlignedString(List<byte> bytes, string value)
    {
        var encoded = Encoding.ASCII.GetBytes(value);
        WriteInt32(bytes, encoded.Length);
        bytes.AddRange(encoded);
        while (bytes.Count % 4 != 0)
        {
            bytes.Add(0);
        }
    }

    private static void WriteInt32(List<byte> bytes, int value)
    {
        Span<byte> encoded = stackalloc byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(encoded, value);
        bytes.AddRange(encoded.ToArray());
    }

    private static void WriteInt64(List<byte> bytes, long value)
    {
        Span<byte> encoded = stackalloc byte[8];
        BinaryPrimitives.WriteInt64LittleEndian(encoded, value);
        bytes.AddRange(encoded.ToArray());
    }
}
