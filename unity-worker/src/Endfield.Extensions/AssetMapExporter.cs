using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using AnimeStudio;

namespace Vfs.Endfield.Extensions;

public sealed record AssetMapExportRequest(
    string InputPath,
    string OutputDirectory,
    string SourceLabel,
    IReadOnlyList<string> IncludedTypes);

public sealed record AssetMapExportResult(
    int ArtifactCount,
    IReadOnlyList<ExportArtifact> Artifacts,
    int EntryCount,
    IReadOnlyList<string> IncludedTypes);

/// <summary>
/// 为单个 Bundle 建立对象索引。它不生成或消费跨 Bundle CABMap。
/// </summary>
public static class AssetMapExporter
{
    public static AssetMapExportResult Export(AssetMapExportRequest request)
    {
        var (inputPath, outputDirectory) = ExportRequestGuard.Prepare(
            request.InputPath,
            request.OutputDirectory);
        var sourceLabel = ParseSourceLabel(request.SourceLabel);
        var includedTypes = ParseIncludedTypes(request.IncludedTypes);
        var game = GameManager.GetGame(GameType.ArknightsEndfield)
            ?? throw new MonoBehaviourExportException(
                "game_profile_missing",
                "AnimeStudio 核心未提供 ArknightsEndfield 游戏配置。");

        const string mapName = "asset-map";
        const string relativePath = "asset-map.json";
        EndfieldAssetTypeProfile.Configure();
        AssetsHelper.Minimal = true;
        AssetsHelper.Paths = new Dictionary<ulong, string>();
        try
        {
            AssetsHelper.BuildAssetMap(
                [inputPath],
                mapName,
                game,
                outputDirectory,
                ExportListType.JSON,
                includedTypes).GetAwaiter().GetResult();
        }
        finally
        {
            AssetsHelper.Clear();
        }

        var outputPath = Path.Combine(outputDirectory, relativePath);
        if (!File.Exists(outputPath))
        {
            throw new MonoBehaviourExportException(
                "asset_map_failed",
                "AssetMap 构建没有产生预期 JSON；上游实现可能在内部吞掉了解析错误。");
        }
        var document = JsonNode.Parse(File.ReadAllText(outputPath))?.AsObject()
            ?? throw new MonoBehaviourExportException(
                "invalid_asset_map",
                "AssetMap JSON 根节点不是对象。");
        if (document["AssetEntries"] is not JsonArray entries)
        {
            throw new MonoBehaviourExportException(
                "invalid_asset_map",
                "AssetMap JSON 缺少 AssetEntries 数组。");
        }

        // AnimeStudio 会把临时输入文件的绝对路径写进 Source。物理路径不是产品数据，
        // 必须在 worker 边界替换为调用方提供的稳定逻辑标识，避免缓存随机器和 run 漂移。
        foreach (var entryNode in entries)
        {
            if (entryNode is not JsonObject entry)
            {
                throw new MonoBehaviourExportException(
                    "invalid_asset_map",
                    "AssetMap 的 AssetEntries 包含非对象元素。");
            }
            entry["Source"] = sourceLabel;
        }
        File.WriteAllText(
            outputPath,
            document.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n");

        var bytes = File.ReadAllBytes(outputPath);
        var artifact = new ExportArtifact(
            relativePath,
            bytes.Length,
            Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant());
        return new AssetMapExportResult(
            1,
            [artifact],
            entries.Count,
            includedTypes.Select(type => type.ToString()).ToArray());
    }

    private static string ParseSourceLabel(string? value)
    {
        var label = value?.Trim();
        if (string.IsNullOrEmpty(label))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "AssetMap 必须提供稳定的 sourceLabel。");
        }
        return label;
    }

    private static ClassIDType[] ParseIncludedTypes(IReadOnlyList<string>? names)
    {
        if (names is null || names.Count == 0)
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "AssetMap 必须显式声明 includedTypes。");
        }
        var types = new List<ClassIDType>();
        foreach (var name in names)
        {
            if (!Enum.TryParse<ClassIDType>(name, true, out var type) ||
                !Enum.IsDefined(type) ||
                types.Contains(type))
            {
                throw new MonoBehaviourExportException(
                    "invalid_asset_type",
                    $"AssetMap includedTypes 包含未知或重复类型：{name}");
            }
            types.Add(type);
        }
        return types.ToArray();
    }
}
