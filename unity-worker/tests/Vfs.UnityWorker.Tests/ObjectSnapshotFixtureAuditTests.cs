using System.Text.Json;
using System.Security.Cryptography;
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

            var legacyTextureRoot = Path.Combine(fixtureRoot, "textures");
            var textureSelections = ReadTextureSelections(legacyRoot, legacyTextureRoot);
            if (textureSelections.Count > 0)
            {
                var textureRoot = Path.Combine(temporaryRoot, "textures");
                var textureResult = ObjectSnapshotExporter.ExportIdentifiedTextures(
                    new IdentifiedTextureExportRequest(
                        inputs,
                        Path.Combine(cabRoot, "cab-map.json"),
                        "fixture:primary",
                        textureSelections,
                        textureRoot));
                Assert.AreEqual(textureSelections.Count, textureResult.ArtifactCount);
                foreach (var artifact in textureResult.Artifacts)
                {
                    var suffix = $"_p{unchecked((ulong)artifact.PathId):X16}.png";
                    var legacyMatches = Directory.GetFiles(
                            legacyTextureRoot,
                            "*.png",
                            SearchOption.AllDirectories)
                        .Where(path => path.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                        .ToArray();
                    Assert.AreEqual(1, legacyMatches.Length, $"legacy texture match for {suffix}");
                    var currentPath = Path.Combine(
                        textureRoot,
                        artifact.RelativePath.Replace('/', Path.DirectorySeparatorChar));
                    CollectionAssert.AreEqual(
                        SHA256.HashData(File.ReadAllBytes(legacyMatches[0])),
                        SHA256.HashData(File.ReadAllBytes(currentPath)),
                        $"texture bytes differ for {suffix}");
                }
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

    private static IReadOnlyList<IdentifiedTextureSelection> ReadTextureSelections(
        string root,
        string legacyTextureRoot)
    {
        var textureFiles = Directory.GetFiles(
            legacyTextureRoot,
            "*.png",
            SearchOption.AllDirectories);
        var pathIds = new HashSet<long>();
        foreach (var textureFile in textureFiles)
        {
            var name = Path.GetFileNameWithoutExtension(textureFile);
            var marker = name.LastIndexOf("_p", StringComparison.OrdinalIgnoreCase);
            if (marker >= 0 &&
                ulong.TryParse(
                    name[(marker + 2)..],
                    System.Globalization.NumberStyles.HexNumber,
                    null,
                    out var value))
            {
                pathIds.Add(unchecked((long)value));
            }
        }
        var values = new Dictionary<string, IdentifiedTextureSelection>(StringComparer.OrdinalIgnoreCase);
        foreach (var path in Directory.GetFiles(root, "*.json", SearchOption.AllDirectories))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            if (!document.RootElement.TryGetProperty("$animestudio", out var metadata) ||
                !metadata.TryGetProperty("pptrReferences", out var references))
            {
                continue;
            }
            foreach (var reference in references.EnumerateArray())
            {
                if (!reference.TryGetProperty("targetType", out var targetType) ||
                    targetType.GetString() != "Texture2D" ||
                    !reference.TryGetProperty("targetSourceFile", out var sourceFile) ||
                    !reference.TryGetProperty("targetPathId", out var pathId))
                {
                    continue;
                }
                var source = sourceFile.GetString()!;
                var id = pathId.GetInt64();
                if (pathIds.Contains(id))
                {
                    values.TryAdd($"{source}|{id}", new IdentifiedTextureSelection(source, id));
                }
            }
        }
        return values.Values.ToArray();
    }
}
