using System.Text.Json;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class WorkerProtocolTests
{
    [TestMethod]
    public void HandshakeReportsOnlyImplementedCapabilities()
    {
        var response = WorkerProtocol.Handle("handshake");

        Assert.IsTrue(response.Ok);
        Assert.IsNull(response.Error);
        var payload = JsonSerializer.Serialize(response, WorkerProtocol.JsonOptions);
        StringAssert.Contains(payload, "vfs-unity-worker");
        StringAssert.Contains(payload, WorkerProtocol.AnimeStudioUpstreamCommit);
        StringAssert.Contains(
            payload,
            "\"capabilities\":[\"handshake\",\"exportMonoBehaviourRaw\",\"exportMonoBehaviourTypeTreeDump\",\"decodeProjectileComponent\",\"buildAssetMap\",\"buildCabMap\",\"exportObjectSnapshots\",\"exportIdentifiedTextures\"]");
    }

    [TestMethod]
    public void UnknownOperationReturnsStableStructuredError()
    {
        var response = WorkerProtocol.Handle("not-a-real-operation");

        Assert.IsFalse(response.Ok);
        Assert.IsNull(response.Result);
        Assert.IsNotNull(response.Error);
        Assert.AreEqual("unknown_operation", response.Error.Code);
        Assert.IsFalse(response.Error.Retryable);
    }

    [TestMethod]
    public void RequestRejectsUnsupportedProtocolWithRequestIdentity()
    {
        var response = WorkerProtocol.HandleRequestJson("""
            {
              "protocolVersion": "0.0.0",
              "requestId": "request-17",
              "operation": "exportMonoBehaviourRaw",
              "arguments": {}
            }
            """);

        Assert.IsFalse(response.Ok);
        Assert.AreEqual("request-17", response.RequestId);
        Assert.AreEqual("unsupported_protocol", response.Error?.Code);
    }

    [TestMethod]
    public void RequestRejectsMalformedJson()
    {
        var response = WorkerProtocol.HandleRequestJson("{ definitely-not-json");

        Assert.IsFalse(response.Ok);
        Assert.AreEqual("invalid_request", response.Error?.Code);
    }

    [TestMethod]
    public void RawExportReportsMissingInputWithoutCreatingOutput()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var output = Path.Combine(root, "output");
        try
        {
            var response = WorkerProtocol.HandleRequestJson($$"""
                {
                  "protocolVersion": "1.0.0",
                  "requestId": "missing-input",
                  "operation": "exportMonoBehaviourRaw",
                  "arguments": {
                    "inputPath": "{{Path.Combine(root, "missing.ab").Replace("\\", "\\\\")}}",
                    "outputDirectory": "{{output.Replace("\\", "\\\\")}}",
                    "container": null
                  }
                }
                """);

            Assert.IsFalse(response.Ok);
            Assert.AreEqual("input_not_found", response.Error?.Code);
            Assert.IsFalse(Directory.Exists(output));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }

    [TestMethod]
    public void TypeTreeDumpReportsMissingInputThroughTheSameSelectionBoundary()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var output = Path.Combine(root, "output");
        try
        {
            var response = WorkerProtocol.HandleRequestJson($$"""
                {
                  "protocolVersion": "1.0.0",
                  "requestId": "missing-dump-input",
                  "operation": "exportMonoBehaviourTypeTreeDump",
                  "arguments": {
                    "inputPath": "{{Path.Combine(root, "missing.ab").Replace("\\", "\\\\")}}",
                    "outputDirectory": "{{output.Replace("\\", "\\\\")}}",
                    "container": null
                  }
                }
                """);

            Assert.IsFalse(response.Ok);
            Assert.AreEqual("missing-dump-input", response.RequestId);
            Assert.AreEqual("input_not_found", response.Error?.Code);
            Assert.IsFalse(Directory.Exists(output));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }

    [TestMethod]
    public void RawExportRefusesNonEmptyOutputDirectory()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var input = Path.Combine(root, "input.ab");
        var output = Path.Combine(root, "output");
        try
        {
            Directory.CreateDirectory(output);
            File.WriteAllBytes(input, [0x00]);
            File.WriteAllText(Path.Combine(output, "existing.txt"), "owned by caller");

            var exception = Assert.ThrowsException<MonoBehaviourExportException>(() =>
                MonoBehaviourRawExporter.Export(new MonoBehaviourRawExportRequest(
                    input,
                    output,
                    null)));

            Assert.AreEqual("output_not_empty", exception.Code);
            Assert.AreEqual("owned by caller", File.ReadAllText(Path.Combine(output, "existing.txt")));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }

    [TestMethod]
    public void AssetMapRejectsUnknownIncludedTypeBeforeParsingBundle()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var input = Path.Combine(root, "input.ab");
        var output = Path.Combine(root, "output");
        try
        {
            Directory.CreateDirectory(root);
            File.WriteAllBytes(input, [0x00]);

            var exception = Assert.ThrowsException<MonoBehaviourExportException>(() =>
                AssetMapExporter.Export(new AssetMapExportRequest(
                    input,
                    output,
                    "fixture:invalid-type",
                    ["DefinitelyNotAUnityType"])));

            Assert.AreEqual("invalid_asset_type", exception.Code);
            Assert.IsTrue(Directory.Exists(output));
            Assert.AreEqual(0, Directory.GetFileSystemEntries(output).Length);
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }

    [TestMethod]
    public void CabMapRejectsDuplicateInputIdsBeforeParsingBundles()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var input = Path.Combine(root, "input.ab");
        var output = Path.Combine(root, "output");
        try
        {
            Directory.CreateDirectory(root);
            File.WriteAllBytes(input, [0x00]);

            var exception = Assert.ThrowsException<MonoBehaviourExportException>(() =>
                CabMapExporter.Export(new CabMapExportRequest(
                    [
                        new CabMapInput("same", input),
                        new CabMapInput("same", input),
                    ],
                    output)));

            Assert.AreEqual("duplicate_input_id", exception.Code);
            Assert.IsFalse(Directory.Exists(output));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }

    [TestMethod]
    public void ObjectSnapshotRejectsUnknownPrimaryInputBeforeCreatingOutput()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var input = Path.Combine(root, "input.ab");
        var cabMap = Path.Combine(root, "cab-map.json");
        var output = Path.Combine(root, "output");
        try
        {
            Directory.CreateDirectory(root);
            File.WriteAllBytes(input, [0x00]);
            File.WriteAllText(cabMap, "{\"schemaVersion\":1,\"entries\":[]}");

            var exception = Assert.ThrowsException<MonoBehaviourExportException>(() =>
                ObjectSnapshotExporter.Export(new ObjectSnapshotExportRequest(
                    [new ObjectSnapshotInput("bundle:1", input)],
                    cabMap,
                    "bundle:missing",
                    ["bundle:1"],
                    ["GameObject"],
                    [],
                    output)));

            Assert.AreEqual("unknown_primary_input", exception.Code);
            Assert.IsFalse(Directory.Exists(output));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }

    [TestMethod]
    public void IdentifiedTextureRejectsDuplicateSelectionBeforeCreatingOutput()
    {
        var root = Path.Combine(Path.GetTempPath(), $"vfs-worker-test-{Guid.NewGuid():N}");
        var input = Path.Combine(root, "input.ab");
        var cabMap = Path.Combine(root, "cab-map.json");
        var output = Path.Combine(root, "output");
        try
        {
            Directory.CreateDirectory(root);
            File.WriteAllBytes(input, [0x00]);
            File.WriteAllText(cabMap, "{\"schemaVersion\":1,\"entries\":[]}");

            var exception = Assert.ThrowsException<MonoBehaviourExportException>(() =>
                ObjectSnapshotExporter.ExportIdentifiedTextures(
                    new IdentifiedTextureExportRequest(
                        [new ObjectSnapshotInput("bundle:1", input)],
                        cabMap,
                        "bundle:1",
                        [
                            new IdentifiedTextureSelection("CAB-test", 7),
                            new IdentifiedTextureSelection("cab-TEST", 7),
                        ],
                        output)));

            Assert.AreEqual("invalid_texture_selection", exception.Code);
            Assert.IsFalse(Directory.Exists(output));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, true);
            }
        }
    }
}
