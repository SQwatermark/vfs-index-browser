using System.Security.Cryptography;
using AnimeStudio;
using SixLabors.ImageSharp;
using UnityObject = AnimeStudio.Object;

namespace Vfs.Endfield.Extensions;

public sealed record CubemapFaceExportRequest(
    string InputPath,
    string OutputDirectory,
    string Container);

public sealed record CubemapFaceArtifact(
    string RelativePath,
    string Face,
    string SourceFile,
    long PathId,
    string Name,
    int Width,
    int Height,
    long ByteCount,
    string Sha256);

public sealed record CubemapFaceExportResult(
    int ArtifactCount,
    IReadOnlyList<CubemapFaceArtifact> Artifacts);

public static class CubemapFaceExporter
{
    private static readonly string[] FaceNames =
    [
        "PositiveX", "NegativeX", "PositiveY",
        "NegativeY", "PositiveZ", "NegativeZ",
    ];

    public static CubemapFaceExportResult Export(CubemapFaceExportRequest request)
    {
        var (inputPath, outputDirectory) = ExportRequestGuard.Prepare(
            request.InputPath,
            request.OutputDirectory);
        var container = request.Container?.Trim().Replace('\\', '/');
        if (string.IsNullOrEmpty(container))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "Cubemap 导出必须提供精确 container。");
        }
        var game = GameManager.GetGame(GameType.ArknightsEndfield)
            ?? throw new MonoBehaviourExportException(
                "game_profile_missing",
                "AnimeStudio 核心未提供 ArknightsEndfield 游戏配置。");

        EndfieldAssetTypeProfile.Configure();
        var manager = new AssetsManager
        {
            Game = game,
            Silent = true,
            ResolveDependencies = false,
        };
        try
        {
            manager.LoadFiles(inputPath);
            var containers = BuildContainerMap(manager.assetsFileList);
            var matches = manager.assetsFileList
                .SelectMany(file => file.Objects.OfType<Cubemap>())
                .Where(cubemap =>
                    containers.TryGetValue(cubemap, out var value) &&
                    string.Equals(value.Replace('\\', '/'), container, StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new MonoBehaviourExportException(
                    "cubemap_not_found",
                    $"精确 container 应匹配一个 Cubemap，实际为 {matches.Length}：{container}");
            }

            var cubemap = matches[0];
            var imageData = cubemap.image_data.GetData();
            var faceSize = cubemap.m_CompleteImageSize;
            if (cubemap.m_ImageCount != FaceNames.Length ||
                faceSize <= 0 ||
                imageData.Length != faceSize * cubemap.m_ImageCount)
            {
                throw new MonoBehaviourExportException(
                    "invalid_cubemap_layout",
                    $"Cubemap 不是六份连续完整 mip 链：{cubemap.Name}");
            }

            var artifacts = new List<CubemapFaceArtifact>(FaceNames.Length);
            for (var faceIndex = 0; faceIndex < FaceNames.Length; faceIndex++)
            {
                var faceData = new byte[faceSize];
                Buffer.BlockCopy(imageData, faceIndex * faceSize, faceData, 0, faceSize);
                using var image = cubemap.ConvertToImage(faceData, false)
                    ?? throw new MonoBehaviourExportException(
                        "cubemap_decode_failed",
                        $"Cubemap 面无法解码：{cubemap.Name} / {FaceNames[faceIndex]}");
                var relativePath = Path.Combine(
                    "Cubemap",
                    $"{SafeName(cubemap.Name)}_{FaceNames[faceIndex]}.png");
                var outputPath = Path.Combine(outputDirectory, relativePath);
                Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
                using (var stream = new FileStream(
                    outputPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None))
                {
                    image.SaveAsPng(stream);
                }
                var info = new FileInfo(outputPath);
                using var hashStream = File.OpenRead(outputPath);
                artifacts.Add(new CubemapFaceArtifact(
                    relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                    FaceNames[faceIndex],
                    cubemap.assetsFile.fileName,
                    cubemap.m_PathID,
                    cubemap.Name,
                    cubemap.m_Width,
                    cubemap.m_Height,
                    info.Length,
                    Convert.ToHexString(SHA256.HashData(hashStream)).ToLowerInvariant()));
            }
            return new CubemapFaceExportResult(artifacts.Count, artifacts);
        }
        finally
        {
            manager.Clear();
        }
    }

    private static Dictionary<UnityObject, string> BuildContainerMap(
        IReadOnlyList<SerializedFile> files)
    {
        var containers = new Dictionary<UnityObject, string>();
        foreach (var bundle in files.SelectMany(file => file.Objects).OfType<AssetBundle>())
        {
            foreach (var entry in bundle.m_Container)
            {
                var end = checked(entry.Value.preloadIndex + entry.Value.preloadSize);
                for (var index = entry.Value.preloadIndex; index < end; index++)
                {
                    if (index < 0 || index >= bundle.m_PreloadTable.Count)
                    {
                        throw new MonoBehaviourExportException(
                            "invalid_container_range",
                            $"AssetBundle container 的 preload 范围越界：{entry.Key}");
                    }
                    if (bundle.m_PreloadTable[index].TryGet(out var asset))
                    {
                        containers[asset] = entry.Key;
                    }
                }
            }
        }
        return containers;
    }

    private static string SafeName(string value)
    {
        var name = string.IsNullOrWhiteSpace(value) ? "Cubemap" : value;
        foreach (var character in Path.GetInvalidFileNameChars())
        {
            name = name.Replace(character, '_');
        }
        return name.Length < 180 ? name : name[..180];
    }
}
