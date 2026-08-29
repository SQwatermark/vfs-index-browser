using System.Security.Cryptography;
using AnimeStudio;
using SixLabors.ImageSharp;
using UnityObject = AnimeStudio.Object;

namespace Vfs.Endfield.Extensions;

public sealed record BundlePreviewMediaExportRequest(
    string InputPath,
    string OutputDirectory,
    IReadOnlyList<string> IncludedTypes);

public sealed record BundlePreviewMediaArtifact(
    string RelativePath,
    string Type,
    string SourceFile,
    long PathId,
    string Name,
    string Container,
    long ByteCount,
    string Sha256);

public sealed record BundlePreviewMediaSkip(
    string Type,
    string SourceFile,
    long PathId,
    string Name,
    string Container,
    string Reason);

public sealed record BundlePreviewMediaExportResult(
    int ArtifactCount,
    IReadOnlyList<BundlePreviewMediaArtifact> Artifacts,
    int SkippedCount,
    IReadOnlyList<BundlePreviewMediaSkip> Skipped,
    IReadOnlyList<string> IncludedTypes);

/// <summary>
/// 导出浏览器直接预览的、无需附加原生运行时的 Bundle 媒体。这里的类型集合是协议白名单，
/// 不是 AnimeStudio Convert 的任意类型转发；Sprite、AudioClip 和 AnimationClip 各自保留
/// 独立协议边界。
/// </summary>
public static class BundlePreviewMediaExporter
{
    private static readonly IReadOnlyDictionary<string, ClassIDType> SupportedTypes =
        new Dictionary<string, ClassIDType>(StringComparer.Ordinal)
        {
            [nameof(ClassIDType.Texture2D)] = ClassIDType.Texture2D,
            [nameof(ClassIDType.TextAsset)] = ClassIDType.TextAsset,
            [nameof(ClassIDType.VideoClip)] = ClassIDType.VideoClip,
        };

    public static BundlePreviewMediaExportResult Export(
        BundlePreviewMediaExportRequest request)
    {
        var includedTypes = ParseIncludedTypes(request.IncludedTypes);
        var (inputPath, outputDirectory) = ExportRequestGuard.Prepare(
            request.InputPath,
            request.OutputDirectory);
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
            var selected = manager.assetsFileList
                .SelectMany(file => file.Objects)
                .Where(asset => includedTypes.Contains(asset.type))
                .OrderBy(asset => asset.assetsFile.fileName, StringComparer.OrdinalIgnoreCase)
                .ThenBy(asset => asset.m_PathID)
                .ToArray();
            var artifacts = new List<BundlePreviewMediaArtifact>(selected.Length);
            var skipped = new List<BundlePreviewMediaSkip>();
            foreach (var asset in selected)
            {
                var container = containers.GetValueOrDefault(asset, string.Empty);
                var relativePath = ExportOne(asset, container, outputDirectory);
                if (relativePath is null)
                {
                    skipped.Add(new BundlePreviewMediaSkip(
                        asset.type.ToString(),
                        asset.assetsFile.fileName,
                        asset.m_PathID,
                        asset.Name,
                        container,
                        SkipReason(asset)));
                    continue;
                }
                var outputPath = Path.Combine(outputDirectory, relativePath);
                var info = new FileInfo(outputPath);
                using var hashStream = File.OpenRead(outputPath);
                artifacts.Add(new BundlePreviewMediaArtifact(
                    relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                    asset.type.ToString(),
                    asset.assetsFile.fileName,
                    asset.m_PathID,
                    asset.Name,
                    container,
                    info.Length,
                    Convert.ToHexString(SHA256.HashData(hashStream)).ToLowerInvariant()));
            }
            return new BundlePreviewMediaExportResult(
                artifacts.Count,
                artifacts,
                skipped.Count,
                skipped,
                includedTypes.Select(value => value.ToString()).ToArray());
        }
        finally
        {
            manager.Clear();
        }
    }

    private static string? ExportOne(
        UnityObject asset,
        string container,
        string outputDirectory)
    {
        var extension = asset switch
        {
            Texture2D => ".png",
            TextAsset => TextAssetExtension(container),
            VideoClip video => VideoExtension(video.m_OriginalPath),
            _ => throw new InvalidOperationException($"未实现的预览媒体类型：{asset.type}"),
        };
        var relativePath = Path.Combine(
            asset.type.ToString(),
            $"{SafeName(asset.Name, asset.type.ToString())}_p{unchecked((ulong)asset.m_PathID):X16}{extension}");
        var outputPath = Path.Combine(outputDirectory, relativePath);
        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
        switch (asset)
        {
            case Texture2D texture:
                using (var image = texture.ConvertToImage(true))
                {
                    if (image is null)
                    {
                        return null;
                    }
                    using var stream = new FileStream(
                        outputPath,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.None);
                    image.SaveAsPng(stream);
                }
                break;
            case TextAsset text:
                File.WriteAllBytes(outputPath, text.m_Script);
                break;
            case VideoClip video:
                if (video.m_ExternalResources.m_Size <= 0)
                {
                    return null;
                }
                File.WriteAllBytes(outputPath, video.m_VideoData.GetData());
                break;
        }
        return relativePath;
    }

    private static string SkipReason(UnityObject asset) => asset switch
    {
        Texture2D => "texture_decode_failed",
        VideoClip => "video_payload_empty",
        _ => "payload_unavailable",
    };

    private static HashSet<ClassIDType> ParseIncludedTypes(
        IReadOnlyList<string>? values)
    {
        if (values is null || values.Count == 0)
        {
            throw new MonoBehaviourExportException(
                "invalid_asset_type",
                "预览媒体请求必须显式声明 includedTypes。");
        }
        var parsed = new HashSet<ClassIDType>();
        foreach (var value in values)
        {
            var key = value?.Trim() ?? string.Empty;
            if (!SupportedTypes.TryGetValue(key, out var type))
            {
                throw new MonoBehaviourExportException(
                    "invalid_asset_type",
                    $"预览媒体协议不支持类型：{value}");
            }
            parsed.Add(type);
        }
        return parsed;
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
                        containers[asset] = entry.Key.Replace('\\', '/');
                    }
                }
            }
        }
        return containers;
    }

    private static string TextAssetExtension(string container)
    {
        if (string.IsNullOrEmpty(container))
        {
            return ".txt";
        }
        return SafeExtension(Path.GetExtension(container), string.Empty);
    }

    private static string VideoExtension(string originalPath) =>
        SafeExtension(Path.GetExtension(originalPath), string.Empty);

    private static string SafeExtension(string value, string fallback)
    {
        if (string.IsNullOrEmpty(value) ||
            value.Length > 24 ||
            value.Any(character => Path.GetInvalidFileNameChars().Contains(character)))
        {
            return fallback;
        }
        return value;
    }

    private static string SafeName(string value, string fallback)
    {
        var name = string.IsNullOrWhiteSpace(value) ? fallback : value;
        foreach (var character in Path.GetInvalidFileNameChars())
        {
            name = name.Replace(character, '_');
        }
        return name.Length < 160 ? name : name[..160];
    }
}
