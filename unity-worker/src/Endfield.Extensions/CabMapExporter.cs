using System.Security.Cryptography;
using System.Text.Json;
using AnimeStudio;

namespace Vfs.Endfield.Extensions;

public sealed record CabMapInput(string InputId, string InputPath);

public sealed record CabMapExportRequest(
    IReadOnlyList<CabMapInput> Inputs,
    string OutputDirectory);

public sealed record CabMapEntry(
    string Cab,
    string InputId,
    long SerializedFileOffset,
    IReadOnlyList<string> Dependencies);

public sealed record CabMapDocument(
    int SchemaVersion,
    IReadOnlyList<CabMapEntry> Entries);

public sealed record CabMapExportResult(
    int ArtifactCount,
    IReadOnlyList<ExportArtifact> Artifacts,
    int EntryCount,
    int InputCount);

/// <summary>
/// 从显式的多 Bundle 输入建立 CAB 依赖映射。产物只保存稳定 inputId，不保存机器路径，
/// 也不读写 AnimeStudio 的进程级 Maps 目录。
/// </summary>
public static class CabMapExporter
{
    public static CabMapExportResult Export(CabMapExportRequest request)
    {
        var inputs = PrepareInputs(request.Inputs);
        var outputDirectory = ExportRequestGuard.PrepareOutput(request.OutputDirectory);
        var game = GameManager.GetGame(GameType.ArknightsEndfield)
            ?? throw new MonoBehaviourExportException(
                "game_profile_missing",
                "AnimeStudio 核心未提供 ArknightsEndfield 游戏配置。");

        var entries = new Dictionary<string, CabMapEntry>(StringComparer.OrdinalIgnoreCase);
        foreach (var input in inputs)
        {
            var manager = new AssetsManager
            {
                Silent = true,
                SkipProcess = true,
                ResolveDependencies = false,
                Game = game,
            };
            try
            {
                manager.LoadFiles(input.InputPath);
                var inputAssetsFiles = manager.assetsFileList
                    .Where(assetsFile => Path.GetFullPath(
                        assetsFile.originalPath ?? assetsFile.fullName)
                        .Equals(input.InputPath, StringComparison.OrdinalIgnoreCase))
                    .ToArray();
                if (inputAssetsFiles.Length == 0)
                {
                    throw new MonoBehaviourExportException(
                        "bundle_parse_failed",
                        $"输入没有产生 SerializedFile：{input.InputId}");
                }

                // AssetsManager 读取裸 SerializedFile 时可能顺带发现同目录外部文件。这里只能
                // 归属当前显式输入本身产生的条目，依赖文件必须作为另一项输入单独声明。
                foreach (var assetsFile in inputAssetsFiles)
                {
                    var entry = new CabMapEntry(
                        assetsFile.fileName,
                        input.InputId,
                        assetsFile.offset,
                        assetsFile.m_Externals.Select(external => external.fileName).ToArray());
                    if (!entries.TryAdd(entry.Cab, entry))
                    {
                        var previous = entries[entry.Cab];
                        throw new MonoBehaviourExportException(
                            "cab_collision",
                            $"CAB 名 {entry.Cab} 同时来自 {previous.InputId} 和 {entry.InputId}。");
                    }
                }
            }
            finally
            {
                manager.Clear();
            }
        }

        var orderedEntries = entries.Values
            .OrderBy(entry => entry.Cab, StringComparer.OrdinalIgnoreCase)
            .ThenBy(entry => entry.Cab, StringComparer.Ordinal)
            .ToArray();
        var document = new CabMapDocument(1, orderedEntries);
        const string relativePath = "cab-map.json";
        var outputPath = Path.Combine(outputDirectory, relativePath);
        File.WriteAllText(
            outputPath,
            JsonSerializer.Serialize(document, WorkerJsonOptions) + "\n");
        var bytes = File.ReadAllBytes(outputPath);
        var artifact = new ExportArtifact(
            relativePath,
            bytes.Length,
            Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant());
        return new CabMapExportResult(1, [artifact], orderedEntries.Length, inputs.Count);
    }

    private static IReadOnlyList<(string InputId, string InputPath)> PrepareInputs(
        IReadOnlyList<CabMapInput>? values)
    {
        if (values is null || values.Count == 0)
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "CABMap 必须至少包含一个输入。");
        }

        var inputIds = new HashSet<string>(StringComparer.Ordinal);
        var inputPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var inputs = new List<(string InputId, string InputPath)>();
        foreach (var value in values)
        {
            var inputId = value.InputId?.Trim();
            if (string.IsNullOrEmpty(inputId) || !inputIds.Add(inputId))
            {
                throw new MonoBehaviourExportException(
                    "duplicate_input_id",
                    $"CABMap inputId 为空或重复：{value.InputId}");
            }
            var inputPath = ExportRequestGuard.PrepareInput(value.InputPath);
            if (!inputPaths.Add(inputPath))
            {
                throw new MonoBehaviourExportException(
                    "duplicate_input_path",
                    $"CABMap 输入路径重复：{inputPath}");
            }
            inputs.Add((inputId, inputPath));
        }
        return inputs;
    }

    private static readonly JsonSerializerOptions WorkerJsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };
}
