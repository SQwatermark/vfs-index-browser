using System.Text.Json;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class AssetMapFixtureAuditTests
{
    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void ConfiguredBundleUsesStableSourceLabel()
    {
        var fixture = Environment.GetEnvironmentVariable("VFS_WORKER_ASSET_MAP_FIXTURE");
        if (string.IsNullOrWhiteSpace(fixture))
        {
            Assert.Inconclusive("未配置 AssetMap 真实 Bundle 路径。");
            return;
        }

        var output = Path.Combine(
            Path.GetTempPath(),
            $"vfs-worker-asset-map-{Guid.NewGuid():N}");
        const string sourceLabel = "fixture:asset-map";
        try
        {
            var result = AssetMapExporter.Export(new AssetMapExportRequest(
                fixture,
                output,
                sourceLabel,
                ["Mesh", "Material", "Avatar"]));
            var document = JsonDocument.Parse(
                File.ReadAllBytes(Path.Combine(output, "asset-map.json")));
            var entries = document.RootElement.GetProperty("AssetEntries");

            Assert.IsTrue(result.EntryCount > 0, "样本没有产生可验证的资源条目。");
            Assert.AreEqual(result.EntryCount, entries.GetArrayLength());
            foreach (var entry in entries.EnumerateArray())
            {
                Assert.AreEqual(sourceLabel, entry.GetProperty("Source").GetString());
            }
        }
        finally
        {
            if (Directory.Exists(output))
            {
                Directory.Delete(output, true);
            }
        }
    }

    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void ConfiguredBundleProducesPathFreeCabMap()
    {
        var fixture = Environment.GetEnvironmentVariable("VFS_WORKER_ASSET_MAP_FIXTURE");
        if (string.IsNullOrWhiteSpace(fixture))
        {
            Assert.Inconclusive("未配置 CABMap 真实 Bundle 路径。");
            return;
        }

        var output = Path.Combine(
            Path.GetTempPath(),
            $"vfs-worker-cab-map-{Guid.NewGuid():N}");
        var secondOutput = Path.Combine(
            Path.GetTempPath(),
            $"vfs-worker-cab-map-{Guid.NewGuid():N}");
        const string inputId = "fixture:bundle";
        try
        {
            var result = CabMapExporter.Export(new CabMapExportRequest(
                [new CabMapInput(inputId, fixture)],
                output));
            var document = JsonDocument.Parse(
                File.ReadAllBytes(Path.Combine(output, "cab-map.json")));
            var entries = document.RootElement.GetProperty("entries");

            Assert.IsTrue(result.EntryCount > 0, "样本没有产生 CAB 条目。");
            Assert.AreEqual(result.EntryCount, entries.GetArrayLength());
            foreach (var entry in entries.EnumerateArray())
            {
                Assert.AreEqual(inputId, entry.GetProperty("inputId").GetString());
                Assert.IsTrue(entry.GetProperty("serializedFileOffset").GetInt64() >= 0);
            }
            Assert.IsFalse(
                File.ReadAllText(Path.Combine(output, "cab-map.json"))
                    .Contains(Path.GetFullPath(fixture), StringComparison.OrdinalIgnoreCase),
                "CABMap 产物不应包含物理输入路径。");

            var second = CabMapExporter.Export(new CabMapExportRequest(
                [new CabMapInput(inputId, fixture)],
                secondOutput));
            Assert.AreEqual(
                result.Artifacts[0].Sha256,
                second.Artifacts[0].Sha256,
                "相同逻辑输入在不同 run 目录中应产生相同 CABMap。");
        }
        finally
        {
            if (Directory.Exists(output))
            {
                Directory.Delete(output, true);
            }
            if (Directory.Exists(secondOutput))
            {
                Directory.Delete(secondOutput, true);
            }
        }
    }
}
