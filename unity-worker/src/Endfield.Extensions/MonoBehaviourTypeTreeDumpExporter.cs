using System.Security.Cryptography;
using System.Text;

namespace Vfs.Endfield.Extensions;

public sealed record MonoBehaviourTypeTreeDumpRequest(
    string InputPath,
    string OutputDirectory,
    string? Container);

public sealed record MonoBehaviourTypeTreeDumpResult(
    int ArtifactCount,
    IReadOnlyList<MonoBehaviourTypeTreeDumpArtifact> Artifacts);

public sealed record MonoBehaviourTypeTreeDumpArtifact(
    string RelativePath,
    string SourceFile,
    string PathId,
    string PathIdHex,
    string? Container,
    string Name,
    int ByteCount,
    string Sha256,
    int SerializedByteCount,
    int ConsumedByteCount,
    bool Complete);

/// <summary>
/// 仅使用对象内嵌 TypeTree 生成诊断文本。它不会加载游戏程序集，也不会把不完整读取
/// 伪装成完整的领域对象；Complete 明确报告 TypeTree 是否消费了全部序列化字节。
/// </summary>
public static class MonoBehaviourTypeTreeDumpExporter
{
    public static MonoBehaviourTypeTreeDumpResult Export(
        MonoBehaviourTypeTreeDumpRequest request)
    {
        return MonoBehaviourExportPipeline.Run(
            request.InputPath,
            request.OutputDirectory,
            request.Container,
            static (outputDirectory, selected) =>
            {
                var artifacts = new List<MonoBehaviourTypeTreeDumpArtifact>(selected.Count);
                foreach (var item in selected)
                {
                    var dump = item.Asset.Dump();
                    if (dump is null)
                    {
                        throw new MonoBehaviourExportException(
                            "type_tree_unavailable",
                            $"MonoBehaviour 没有内嵌 TypeTree：{item.Asset.Name}");
                    }

                    var bytes = new UTF8Encoding(false).GetBytes(dump);
                    var consumed = checked((int)(
                        item.Asset.reader.Position - item.Asset.reader.byteStart));
                    var serialized = checked((int)item.Asset.byteSize);
                    var relativePath = Path.Combine(
                        "objects",
                        $"{item.SourceOrdinal:D4}-p{unchecked((ulong)item.Asset.m_PathID):X16}.txt");
                    var outputPath = Path.Combine(outputDirectory, relativePath);
                    Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
                    using (var stream = new FileStream(
                        outputPath,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.None))
                    {
                        stream.Write(bytes);
                    }

                    artifacts.Add(new MonoBehaviourTypeTreeDumpArtifact(
                        relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                        item.Asset.assetsFile.fileName,
                        item.Asset.m_PathID.ToString(
                            System.Globalization.CultureInfo.InvariantCulture),
                        $"0x{unchecked((ulong)item.Asset.m_PathID):X16}",
                        item.Container,
                        item.Asset.Name,
                        bytes.Length,
                        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),
                        serialized,
                        consumed,
                        consumed == serialized));
                }

                return new MonoBehaviourTypeTreeDumpResult(artifacts.Count, artifacts);
            });
    }
}
