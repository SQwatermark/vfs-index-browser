using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker;

/// <summary>
/// VFS 自有的 worker 协议入口。这里故意不暴露 AnimeStudio CLI 参数，后续能力只能以
/// VFS 定义的操作加入协议。
/// </summary>
public static class WorkerProtocol
{
    public const string ProtocolName = "vfs-unity-worker";
    public const string ProtocolVersion = "1.0.0";
    public const string WorkerVersion = "0.9.0";
    public const string AnimeStudioUpstreamCommit =
        "8cdec963c4e187ea0a4a339b8969844a9574638b";

    public static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static WorkerResponse Handle(string operation)
    {
        if (string.Equals(operation, "handshake", StringComparison.Ordinal))
        {
            return WorkerResponse.Success(new HandshakeResult(
                new ProtocolIdentity(ProtocolName, ProtocolVersion),
                WorkerVersion,
                new BuildIdentity("vfs-index-browser", AnimeStudioUpstreamCommit),
                new RuntimeIdentity(
                    RuntimeInformation.FrameworkDescription,
                    RuntimeInformation.OSDescription,
                    RuntimeInformation.ProcessArchitecture.ToString()),
                [
                    "handshake",
                    "exportMonoBehaviourRaw",
                    "exportMonoBehaviourTypeTreeDump",
                    "decodeProjectileComponent",
                    "buildAssetMap",
                    "buildCabMap",
                    "exportObjectSnapshots",
                    "exportIdentifiedTextures",
                    "exportCubemapFaces",
                    "exportBundlePreviewMedia",
                ]));
        }

        return WorkerResponse.Failure(new WorkerError(
            "unknown_operation",
            string.IsNullOrEmpty(operation)
                ? "缺少 worker 操作名。"
                : $"未知的 worker 操作：{operation}",
            false));
    }

    public static WorkerResponse HandleRequestJson(string json)
    {
        WorkerRequest? request = null;
        try
        {
            request = JsonSerializer.Deserialize<WorkerRequest>(json, JsonOptions);
            if (request is null || string.IsNullOrWhiteSpace(request.RequestId))
            {
                return WorkerResponse.Failure(new WorkerError(
                    "invalid_request",
                    "请求必须包含非空 requestId。",
                    false));
            }
            if (!string.Equals(request.ProtocolVersion, ProtocolVersion, StringComparison.Ordinal))
            {
                return WorkerResponse.Failure(new WorkerError(
                    "unsupported_protocol",
                    $"不支持的协议版本：{request.ProtocolVersion}",
                    false), request.RequestId);
            }

            return request.Operation switch
            {
                "exportMonoBehaviourRaw" => HandleMonoBehaviourRawExport(request),
                "exportMonoBehaviourTypeTreeDump" => HandleMonoBehaviourTypeTreeDumpExport(request),
                "decodeProjectileComponent" => HandleProjectileComponentExport(request),
                "buildAssetMap" => HandleAssetMapExport(request),
                "buildCabMap" => HandleCabMapExport(request),
                "exportObjectSnapshots" => HandleObjectSnapshotExport(request),
                "exportIdentifiedTextures" => HandleIdentifiedTextureExport(request),
                "exportCubemapFaces" => HandleCubemapFaceExport(request),
                "exportBundlePreviewMedia" => HandleBundlePreviewMediaExport(request),
                _ => WorkerResponse.Failure(new WorkerError(
                    "unknown_operation",
                    $"未知的 worker 操作：{request.Operation}",
                    false), request.RequestId),
            };
        }
        catch (JsonException exception)
        {
            return WorkerResponse.Failure(new WorkerError(
                "invalid_request",
                $"请求 JSON 无效：{exception.Message}",
                false), request?.RequestId);
        }
        catch (MonoBehaviourExportException exception)
        {
            return WorkerResponse.Failure(new WorkerError(
                exception.Code,
                exception.Message,
                false), request?.RequestId);
        }
        catch (Exception exception) when (
            exception is IOException or UnauthorizedAccessException or ArgumentException)
        {
            return WorkerResponse.Failure(new WorkerError(
                "invalid_input",
                exception.Message,
                false), request?.RequestId);
        }
        catch (Exception exception)
        {
            return WorkerResponse.Failure(new WorkerError(
                "internal_error",
                exception.Message,
                false), request?.RequestId);
        }
    }

    private static WorkerResponse HandleMonoBehaviourRawExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<MonoBehaviourRawExportRequest>(JsonOptions)
            ?? throw new JsonException("exportMonoBehaviourRaw 缺少 arguments。");
        var result = MonoBehaviourRawExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleMonoBehaviourTypeTreeDumpExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<MonoBehaviourTypeTreeDumpRequest>(JsonOptions)
            ?? throw new JsonException("exportMonoBehaviourTypeTreeDump 缺少 arguments。");
        var result = MonoBehaviourTypeTreeDumpExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleProjectileComponentExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<ProjectileComponentExportRequest>(JsonOptions)
            ?? throw new JsonException("decodeProjectileComponent 缺少 arguments。");
        var result = ProjectileComponentExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleAssetMapExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<AssetMapExportRequest>(JsonOptions)
            ?? throw new JsonException("buildAssetMap 缺少 arguments。");
        var result = AssetMapExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleCabMapExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<CabMapExportRequest>(JsonOptions)
            ?? throw new JsonException("buildCabMap 缺少 arguments。");
        var result = CabMapExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleObjectSnapshotExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<ObjectSnapshotExportRequest>(JsonOptions)
            ?? throw new JsonException("exportObjectSnapshots 缺少 arguments。");
        var result = ObjectSnapshotExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleIdentifiedTextureExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<IdentifiedTextureExportRequest>(JsonOptions)
            ?? throw new JsonException("exportIdentifiedTextures 缺少 arguments。");
        var result = ObjectSnapshotExporter.ExportIdentifiedTextures(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleCubemapFaceExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<CubemapFaceExportRequest>(JsonOptions)
            ?? throw new JsonException("exportCubemapFaces 缺少 arguments。");
        var result = CubemapFaceExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }

    private static WorkerResponse HandleBundlePreviewMediaExport(WorkerRequest request)
    {
        var arguments = request.Arguments.Deserialize<BundlePreviewMediaExportRequest>(JsonOptions)
            ?? throw new JsonException("exportBundlePreviewMedia 缺少 arguments。");
        var result = BundlePreviewMediaExporter.Export(arguments);
        return WorkerResponse.Success(result, request.RequestId);
    }
}

public sealed record WorkerResponse(string? RequestId, bool Ok, object? Result, WorkerError? Error)
{
    public static WorkerResponse Success(object result, string? requestId = null) =>
        new(requestId, true, result, null);

    public static WorkerResponse Failure(WorkerError error, string? requestId = null) =>
        new(requestId, false, null, error);
}

public sealed record WorkerError(string Code, string Message, bool Retryable);

public sealed record HandshakeResult(
    ProtocolIdentity Protocol,
    string WorkerVersion,
    BuildIdentity Build,
    RuntimeIdentity Runtime,
    IReadOnlyList<string> Capabilities);

public sealed record ProtocolIdentity(string Name, string Version);

public sealed record BuildIdentity(string Product, string AnimeStudioUpstreamCommit);

public sealed record RuntimeIdentity(string Framework, string OperatingSystem, string Architecture);

public sealed record WorkerRequest(
    string ProtocolVersion,
    string RequestId,
    string Operation,
    JsonElement Arguments);
