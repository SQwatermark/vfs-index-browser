using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Vfs.Endfield.Extensions;

public sealed record ProjectileComponentExportRequest(
    string InputPath,
    string OutputDirectory,
    string Container,
    string ExpectedProjectileId);

public sealed record ProjectileComponentExportResult(
    int ArtifactCount,
    IReadOnlyList<ProjectileComponentArtifact> Artifacts);

public sealed record ProjectileComponentArtifact(
    string RelativePath,
    string SourceFile,
    string PathId,
    string PathIdHex,
    string Container,
    string Name,
    string ProjectileId,
    string DecodeStatus,
    int ComponentOffset,
    int ComponentLength,
    int RemainingRawWordCount,
    int ByteCount,
    string Sha256);

/// <summary>
/// 从精确 container 的唯一 MonoBehaviour 中导出聚焦 Projectile 组件 JSON。产物不包含
/// MonoBehaviour 外壳，调用方无需再递归猜测哪个 managed reference 才是目标组件。
/// </summary>
public static class ProjectileComponentExporter
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    public static ProjectileComponentExportResult Export(ProjectileComponentExportRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.Container))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "Projectile 导出必须提供精确 container。");
        }

        return MonoBehaviourExportPipeline.Run(
            request.InputPath,
            request.OutputDirectory,
            request.Container,
            (outputDirectory, selected) =>
            {
                if (selected.Count != 1)
                {
                    throw new MonoBehaviourExportException(
                        "mono_behaviour_not_unique",
                        $"精确 container 应只对应一个 MonoBehaviour，实际为 {selected.Count} 个。");
                }

                var item = selected[0];
                var decoded = ProjectileComponentDecoder.Decode(
                    item.Asset.GetRawData(),
                    request.ExpectedProjectileId);
                var bytes = new UTF8Encoding(false).GetBytes(
                    JsonSerializer.Serialize(decoded.Component, JsonOptions) + "\n");
                const string relativePath = "projectile-component.json";
                var outputPath = Path.Combine(outputDirectory, relativePath);
                using (var stream = new FileStream(
                    outputPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None))
                {
                    stream.Write(bytes);
                }

                var artifact = new ProjectileComponentArtifact(
                    relativePath,
                    item.Asset.assetsFile.fileName,
                    item.Asset.m_PathID.ToString(
                        System.Globalization.CultureInfo.InvariantCulture),
                    $"0x{unchecked((ulong)item.Asset.m_PathID):X16}",
                    item.Container!,
                    item.Asset.Name,
                    request.ExpectedProjectileId,
                    "partial",
                    decoded.ComponentOffset,
                    decoded.ComponentLength,
                    decoded.RemainingRawWordCount,
                    bytes.Length,
                    Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant());
                return new ProjectileComponentExportResult(1, [artifact]);
            });
    }
}
