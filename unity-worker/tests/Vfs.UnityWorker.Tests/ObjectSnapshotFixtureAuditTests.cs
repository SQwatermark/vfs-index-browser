using System.Text.Json;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ObjectSnapshotFixtureAuditTests
{
    [TestMethod]
    [TestCategory("LocalEvidence")]
    public void ConfiguredModelClosureMatchesLegacyObjectIdentitiesAndContainers()
    {
        var configured = Environment.GetEnvironmentVariable(
            "VFS_WORKER_OBJECT_SNAPSHOT_FIXTURE_ROOT");
        if (string.IsNullOrWhiteSpace(configured))
        {
            Assert.Inconclusive("未配置对象快照模型闭包。");
        }

        var fixtureRoot = Path.GetFullPath(configured!);
        var inputRoot = Path.Combine(fixtureRoot, "inputs");
        var legacyRoot = Path.Combine(fixtureRoot, "objects");
        var inputPaths = Directory.GetFiles(inputRoot, "*.ab")
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        Assert.IsTrue(inputPaths.Any(path =>
            Path.GetFileName(path).Equals("entry.ab", StringComparison.OrdinalIgnoreCase)));
        Assert.IsTrue(Directory.Exists(legacyRoot));

        var inputs = inputPaths.Select(path => new ObjectSnapshotInput(
            Path.GetFileName(path).Equals("entry.ab", StringComparison.OrdinalIgnoreCase)
                ? "fixture:primary"
                : $"fixture:{Path.GetFileNameWithoutExtension(path)}",
            path)).ToArray();
        var temporaryRoot = Path.Combine(
            Path.GetTempPath(),
            $"vfs-object-snapshot-audit-{Guid.NewGuid():N}");
        try
        {
            var cabRoot = Path.Combine(temporaryRoot, "cab");
            CabMapExporter.Export(new CabMapExportRequest(
                inputs.Select(value => new CabMapInput(value.InputId, value.InputPath)).ToArray(),
                cabRoot));
            var exportRoot = Path.Combine(temporaryRoot, "objects");
            ObjectSnapshotExporter.Export(new ObjectSnapshotExportRequest(
                inputs,
                Path.Combine(cabRoot, "cab-map.json"),
                "fixture:primary",
                inputs.Select(value => value.InputId).ToArray(),
                [
                    "GameObject", "Transform", "MeshFilter", "MeshRenderer",
                    "SkinnedMeshRenderer", "Mesh", "Material", "Animator", "Avatar",
                ],
                [],
                exportRoot));

            var legacy = ReadIdentities(legacyRoot, excludeLodGroup: true);
            var current = ReadIdentities(exportRoot, excludeLodGroup: false);
            CollectionAssert.IsSubsetOf(legacy.Keys.ToArray(), current.Keys.ToArray());
            foreach (var identity in legacy.Keys)
            {
                Assert.AreEqual(
                    legacy[identity].Container,
                    current[identity].Container,
                    $"container mismatch for {identity}");
            }

            var exportedText = string.Join(
                '\n',
                Directory.GetFiles(exportRoot, "*.json", SearchOption.AllDirectories)
                    .Select(File.ReadAllText));
            foreach (var inputPath in inputPaths)
            {
                Assert.IsFalse(
                    exportedText.Contains(Path.GetFullPath(inputPath), StringComparison.Ordinal),
                    $"snapshot leaked physical input path {inputPath}");
            }
        }
        finally
        {
            if (Directory.Exists(temporaryRoot))
            {
                Directory.Delete(temporaryRoot, true);
            }
        }
    }

    private static Dictionary<string, (string Type, string Container)> ReadIdentities(
        string root,
        bool excludeLodGroup)
    {
        var values = new Dictionary<string, (string Type, string Container)>(StringComparer.Ordinal);
        foreach (var path in Directory.GetFiles(root, "*.json", SearchOption.AllDirectories))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            if (!document.RootElement.TryGetProperty("$animestudio", out var metadata))
            {
                continue;
            }
            var type = metadata.GetProperty("type").GetString() ?? string.Empty;
            if (excludeLodGroup && type == "LODGroup")
            {
                continue;
            }
            var identity = $"{metadata.GetProperty("sourceFile").GetString()}|" +
                metadata.GetProperty("pathId").GetInt64();
            var container = metadata.GetProperty("container").GetString() ?? string.Empty;
            values.Add(identity, (type, container));
        }
        return values;
    }
}
