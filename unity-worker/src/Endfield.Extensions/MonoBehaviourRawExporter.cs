using System.Security.Cryptography;
using AnimeStudio;

namespace Vfs.Endfield.Extensions;

public sealed record MonoBehaviourRawExportRequest(
    string InputPath,
    string OutputDirectory,
    string? Container);

public sealed record MonoBehaviourRawExportResult(
    int ArtifactCount,
    IReadOnlyList<MonoBehaviourRawArtifact> Artifacts);

public sealed record MonoBehaviourRawArtifact(
    string RelativePath,
    string SourceFile,
    string PathId,
    string PathIdHex,
    string? Container,
    string Name,
    int ByteCount,
    string Sha256);

/// <summary>
/// 只负责从单个 Bundle 中提取 MonoBehaviour 原始对象字节。TypeTree 与具体游戏组件解码
/// 属于后续层，不能在这里产生隐式降级。
/// </summary>
public static class MonoBehaviourRawExporter
{
    public static MonoBehaviourRawExportResult Export(MonoBehaviourRawExportRequest request)
    {
        return MonoBehaviourExportPipeline.Run(
            request.InputPath,
            request.OutputDirectory,
            request.Container,
            static (outputDirectory, selected) =>
            {
                var artifacts = new List<MonoBehaviourRawArtifact>(selected.Count);
                foreach (var item in selected)
                {
                    var raw = item.Asset.GetRawData();
                    var relativePath = Path.Combine(
                        "objects",
                        $"{item.SourceOrdinal:D4}-p{unchecked((ulong)item.Asset.m_PathID):X16}.dat");
                    var outputPath = Path.Combine(outputDirectory, relativePath);
                    Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
                    using (var stream = new FileStream(
                        outputPath,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.None))
                    {
                        stream.Write(raw);
                    }

                    artifacts.Add(new MonoBehaviourRawArtifact(
                        relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                        item.Asset.assetsFile.fileName,
                        item.Asset.m_PathID.ToString(System.Globalization.CultureInfo.InvariantCulture),
                        $"0x{unchecked((ulong)item.Asset.m_PathID):X16}",
                        item.Container,
                        item.Asset.Name,
                        raw.Length,
                        Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant()));
                }
                return new MonoBehaviourRawExportResult(artifacts.Count, artifacts);
            });
    }
}
