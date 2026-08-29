using System.Security.Cryptography;
using System.Text.Json;
using AnimeStudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Converters;
using Newtonsoft.Json.Linq;
using SixLabors.ImageSharp;

namespace Vfs.Endfield.Extensions;

public sealed record ObjectSnapshotInput(string InputId, string InputPath);

public sealed record ObjectSnapshotExportRequest(
    IReadOnlyList<ObjectSnapshotInput> Inputs,
    string CabMapPath,
    string PrimaryInputId,
    IReadOnlyList<string> SelectionInputIds,
    IReadOnlyList<string> IncludedTypes,
    IReadOnlyList<string> Containers,
    string OutputDirectory);

public sealed record ObjectSnapshotExportResult(
    int ArtifactCount,
    IReadOnlyList<ExportArtifact> Artifacts,
    int ObjectCount,
    IReadOnlyList<string> IncludedTypes,
    string PrimaryInputId);

public sealed record IdentifiedTextureSelection(string SourceFile, long PathId);

public sealed record IdentifiedTextureExportRequest(
    IReadOnlyList<ObjectSnapshotInput> Inputs,
    string CabMapPath,
    string PrimaryInputId,
    IReadOnlyList<IdentifiedTextureSelection> Selections,
    string OutputDirectory);

public sealed record IdentifiedTextureArtifact(
    string RelativePath,
    string SourceFile,
    long PathId,
    string Name,
    int Width,
    int Height,
    long ByteCount,
    string Sha256);

public sealed record IdentifiedTextureExportResult(
    int ArtifactCount,
    IReadOnlyList<IdentifiedTextureArtifact> Artifacts);

/// <summary>
/// 在一个请求内绑定显式 Bundle 输入、稳定 CABMap 和对象快照。物理路径只用于本次加载，
/// 不进入公共 JSON；对象身份始终是 sourceFile + pathId。
/// </summary>
public static class ObjectSnapshotExporter
{
    public const string Contract = "AnimeStudioObjectSnapshot";
    public const string ContractVersion = "1.0.0";

    private static readonly HashSet<ClassIDType> SupportedTypes =
    [
        ClassIDType.GameObject,
        ClassIDType.Transform,
        ClassIDType.MeshFilter,
        ClassIDType.MeshRenderer,
        ClassIDType.SkinnedMeshRenderer,
        ClassIDType.Mesh,
        ClassIDType.Material,
        ClassIDType.Animator,
        ClassIDType.Avatar,
    ];

    public static ObjectSnapshotExportResult Export(ObjectSnapshotExportRequest request)
    {
        var inputs = PrepareInputs(request.Inputs);
        var inputById = inputs.ToDictionary(value => value.InputId, StringComparer.Ordinal);
        var primaryInputId = request.PrimaryInputId?.Trim();
        if (string.IsNullOrEmpty(primaryInputId) || !inputById.ContainsKey(primaryInputId))
        {
            throw new MonoBehaviourExportException(
                "unknown_primary_input",
                $"对象快照 primaryInputId 不在显式输入中：{request.PrimaryInputId}");
        }
        var selectionInputIds = ParseSelectionInputIds(
            request.SelectionInputIds,
            inputById.Keys);

        var includedTypes = ParseIncludedTypes(request.IncludedTypes);
        var containers = ParseContainers(request.Containers);
        var cabMapPath = ExportRequestGuard.PrepareInput(request.CabMapPath);
        var cabMap = LoadCabMap(cabMapPath, inputById.Keys);
        var outputDirectory = ExportRequestGuard.PrepareOutput(request.OutputDirectory);
        var game = GameManager.GetGame(GameType.ArknightsEndfield)
            ?? throw new MonoBehaviourExportException(
                "game_profile_missing",
                "AnimeStudio 核心未提供 ArknightsEndfield 游戏配置。");

        EndfieldAssetTypeProfile.Configure();
        var manager = new AssetsManager
        {
            Silent = true,
            SkipProcess = false,
            ResolveDependencies = false,
            Game = game,
        };
        try
        {
            // 主输入必须先加载。AssetBundle preload container 可同时给依赖对象附上主资源
            // container；随后依赖自身的 AssetBundle container 会把它改回权威资源路径，
            // 与旧 ObjectJSON 的按需依赖加载顺序一致。
            LoadExplicitInputs(manager, inputs, primaryInputId);
            var sourceInputIds = BindLoadedFiles(manager.assetsFileList, inputs, cabMap);
            var containerByObject = BuildContainerMap(manager.assetsFileList);
            var selected = manager.assetsFileList
                .SelectMany(file => file.Objects)
                .Where(asset => sourceInputIds.TryGetValue(asset.assetsFile, out var inputId) &&
                    selectionInputIds.Contains(inputId))
                .Where(asset => includedTypes.Contains(asset.type))
                .Select(asset => new
                {
                    Asset = asset,
                    Container = containerByObject.GetValueOrDefault(asset, string.Empty),
                })
                .Where(value => containers.Count == 0 || containers.Contains(value.Container))
                .OrderBy(value => value.Asset.assetsFile.fileName, StringComparer.OrdinalIgnoreCase)
                .ThenBy(value => value.Asset.m_PathID)
                .ToArray();

            if (selected.Length == 0)
            {
                throw new MonoBehaviourExportException(
                    "object_selection_empty",
                    "对象快照请求没有选中任何对象；请核对 primaryInputId、类型与 container。" );
            }

            var artifacts = new List<ExportArtifact>(selected.Length);
            foreach (var value in selected)
            {
                var asset = value.Asset;
                var relativePath = Path.Combine(
                    asset.type.ToString(),
                    StableSourceDirectory(asset.assetsFile.fileName),
                    $"p{unchecked((ulong)asset.m_PathID):X16}.json");
                var outputPath = Path.Combine(outputDirectory, relativePath);
                Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
                var payload = BuildSnapshot(
                    asset,
                    value.Container,
                    sourceInputIds[asset.assetsFile],
                    sourceInputIds);
                using (var writer = new StreamWriter(
                    new FileStream(
                        outputPath,
                        FileMode.CreateNew,
                        FileAccess.Write,
                        FileShare.None),
                    new System.Text.UTF8Encoding(false)))
                {
                    writer.Write(payload.ToString(Formatting.Indented));
                    writer.Write('\n');
                }
                var bytes = File.ReadAllBytes(outputPath);
                artifacts.Add(new ExportArtifact(
                    relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                    bytes.Length,
                    Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant()));
            }

            return new ObjectSnapshotExportResult(
                artifacts.Count,
                artifacts,
                artifacts.Count,
                includedTypes.Select(value => value.ToString()).ToArray(),
                primaryInputId);
        }
        finally
        {
            manager.Clear();
        }
    }

    public static IdentifiedTextureExportResult ExportIdentifiedTextures(
        IdentifiedTextureExportRequest request)
    {
        var inputs = PrepareInputs(request.Inputs);
        var inputById = inputs.ToDictionary(value => value.InputId, StringComparer.Ordinal);
        var primaryInputId = request.PrimaryInputId?.Trim();
        if (string.IsNullOrEmpty(primaryInputId) || !inputById.ContainsKey(primaryInputId))
        {
            throw new MonoBehaviourExportException(
                "unknown_primary_input",
                $"纹理导出 primaryInputId 不在显式输入中：{request.PrimaryInputId}");
        }
        var selections = PrepareTextureSelections(request.Selections);
        var cabMapPath = ExportRequestGuard.PrepareInput(request.CabMapPath);
        var cabMap = LoadCabMap(cabMapPath, inputById.Keys);
        var outputDirectory = ExportRequestGuard.PrepareOutput(request.OutputDirectory);
        var game = GameManager.GetGame(GameType.ArknightsEndfield)
            ?? throw new MonoBehaviourExportException(
                "game_profile_missing",
                "AnimeStudio 核心未提供 ArknightsEndfield 游戏配置。");

        EndfieldAssetTypeProfile.Configure();
        var manager = new AssetsManager
        {
            Silent = true,
            SkipProcess = false,
            ResolveDependencies = false,
            Game = game,
        };
        try
        {
            LoadExplicitInputs(manager, inputs, primaryInputId);
            BindLoadedFiles(manager.assetsFileList, inputs, cabMap);
            var texturesByIdentity = manager.assetsFileList
                .SelectMany(file => file.Objects.OfType<Texture2D>())
                .Where(texture => texture.type == ClassIDType.Texture2D)
                .ToDictionary(
                    texture => (texture.assetsFile.fileName, texture.m_PathID),
                    texture => texture,
                    SourcePathIdComparer.Instance);
            var artifacts = new List<IdentifiedTextureArtifact>(selections.Count);
            foreach (var selection in selections)
            {
                if (!texturesByIdentity.TryGetValue(
                    (selection.SourceFile, selection.PathId),
                    out var texture))
                {
                    throw new MonoBehaviourExportException(
                        "texture_not_found",
                        $"找不到精确 Texture2D：{selection.SourceFile} / {selection.PathId}");
                }
                using var image = texture.ConvertToImage(true)
                    ?? throw new MonoBehaviourExportException(
                        "texture_decode_failed",
                        $"Texture2D 无法解码：{selection.SourceFile} / {selection.PathId}");
                var relativePath = Path.Combine(
                    "Texture2D",
                    StableSourceDirectory(texture.assetsFile.fileName),
                    $"{SafeObjectName(texture.Name)}_p{unchecked((ulong)texture.m_PathID):X16}.png");
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
                var outputInfo = new FileInfo(outputPath);
                using var hashStream = File.OpenRead(outputPath);
                artifacts.Add(new IdentifiedTextureArtifact(
                    relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                    texture.assetsFile.fileName,
                    texture.m_PathID,
                    texture.Name,
                    texture.m_Width,
                    texture.m_Height,
                    outputInfo.Length,
                    Convert.ToHexString(SHA256.HashData(hashStream)).ToLowerInvariant()));
            }
            return new IdentifiedTextureExportResult(artifacts.Count, artifacts);
        }
        finally
        {
            manager.Clear();
        }
    }

    private static JObject BuildSnapshot(
        AnimeStudio.Object asset,
        string container,
        string sourceInputId,
        IReadOnlyDictionary<SerializedFile, string> sourceInputIds)
    {
        var serializer = Newtonsoft.Json.JsonSerializer.Create(new JsonSerializerSettings
        {
            Converters = { new StringEnumConverter() },
        });
        object value = asset;
        if (asset is Animator && asset.ToType() is { } animatorValue)
        {
            value = animatorValue;
        }
        var payload = JObject.FromObject(value, serializer);
        var rawData = asset.GetRawData();
        var metadata = new JObject
        {
            ["contract"] = Contract,
            ["version"] = ContractVersion,
            ["pathId"] = asset.m_PathID,
            ["type"] = asset.type.ToString(),
            ["classId"] = (int)asset.type,
            ["name"] = asset.Name,
            ["sourceFile"] = asset.assetsFile.fileName,
            ["sourceInputId"] = sourceInputId,
            ["container"] = container,
            ["byteSize"] = asset.byteSize,
            ["rawDataLength"] = rawData.Length,
            ["rawDataSha256"] = Convert.ToHexString(SHA256.HashData(rawData)).ToLowerInvariant(),
        };
        AddTypeTreeMetadata(metadata, asset.serializedType?.m_Type);
        metadata["externalFiles"] = BuildExternalFiles(asset.assetsFile, sourceInputIds);
        metadata["pptrReferences"] = BuildPPtrReferences(payload, asset.assetsFile, sourceInputIds);
        payload.AddFirst(new JProperty("$animestudio", metadata));
        return payload;
    }

    private static JArray BuildExternalFiles(
        SerializedFile owner,
        IReadOnlyDictionary<SerializedFile, string> sourceInputIds)
    {
        var files = new JArray();
        for (var index = 0; index < owner.m_Externals.Count; index++)
        {
            var external = owner.m_Externals[index];
            var loaded = owner.assetsManager.assetsFileList.FirstOrDefault(
                candidate => candidate.fileName.Equals(
                    external.fileName,
                    StringComparison.OrdinalIgnoreCase));
            var file = new JObject
            {
                ["fileId"] = index + 1,
                ["guid"] = external.guid.ToString("D"),
                ["type"] = external.type,
                ["pathName"] = external.pathName ?? string.Empty,
                ["fileName"] = external.fileName ?? string.Empty,
                ["loaded"] = loaded is not null,
            };
            if (loaded is not null && sourceInputIds.TryGetValue(loaded, out var inputId))
            {
                file["loadedInputId"] = inputId;
            }
            files.Add(file);
        }
        return files;
    }

    private static JArray BuildPPtrReferences(
        JObject payload,
        SerializedFile owner,
        IReadOnlyDictionary<SerializedFile, string> sourceInputIds)
    {
        var references = new JArray();
        foreach (var candidate in payload.DescendantsAndSelf().OfType<JObject>())
        {
            if (!TryReadPPtr(candidate, out var fileId, out var pathId))
            {
                continue;
            }
            var reference = new JObject
            {
                ["path"] = string.IsNullOrEmpty(candidate.Path) ? "$" : $"$.{candidate.Path}",
                ["fileId"] = fileId,
                ["pathId"] = pathId,
            };
            if (TryResolveTarget(owner, fileId, pathId, out var target))
            {
                reference["targetType"] = target.type.ToString();
                reference["targetPathId"] = target.m_PathID;
                reference["targetName"] = target.Name;
                reference["targetSourceFile"] = target.assetsFile.fileName;
                if (sourceInputIds.TryGetValue(target.assetsFile, out var inputId))
                {
                    reference["targetInputId"] = inputId;
                }
            }
            references.Add(reference);
        }
        return references;
    }

    private static bool TryReadPPtr(JObject candidate, out int fileId, out long pathId)
    {
        fileId = 0;
        pathId = 0;
        return candidate["m_FileID"]?.Type == JTokenType.Integer &&
            candidate["m_PathID"]?.Type == JTokenType.Integer &&
            int.TryParse(candidate["m_FileID"]!.ToString(), out fileId) &&
            long.TryParse(candidate["m_PathID"]!.ToString(), out pathId);
    }

    private static bool TryResolveTarget(
        SerializedFile owner,
        int fileId,
        long pathId,
        out AnimeStudio.Object target)
    {
        target = null!;
        if (pathId == 0 || fileId < 0)
        {
            return false;
        }
        var source = owner;
        if (fileId > 0)
        {
            if (fileId > owner.m_Externals.Count)
            {
                return false;
            }
            var externalName = owner.m_Externals[fileId - 1].fileName;
            source = owner.assetsManager.assetsFileList.FirstOrDefault(
                file => file.fileName.Equals(externalName, StringComparison.OrdinalIgnoreCase));
        }
        return source is not null && source.ObjectsDic.TryGetValue(pathId, out target!);
    }

    private static void AddTypeTreeMetadata(JObject metadata, TypeTree? typeTree)
    {
        if (typeTree?.m_Nodes is null)
        {
            metadata["typeTreeSource"] = "none";
            metadata["typeTreeNodeCount"] = 0;
            metadata["typeTreeFieldPaths"] = new JArray();
            return;
        }
        metadata["typeTreeSource"] = "serializedType";
        metadata["typeTreeNodeCount"] = typeTree.m_Nodes.Count;
        var paths = new List<string>();
        var current = new List<string>();
        foreach (var node in typeTree.m_Nodes.Skip(1))
        {
            var depth = Math.Max(0, node.m_Level - 1);
            while (current.Count > depth)
            {
                current.RemoveAt(current.Count - 1);
            }
            current.Add(node.m_Name);
            paths.Add($"{string.Join('.', current)}:{node.m_Type}");
        }
        metadata["typeTreeFieldPaths"] = new JArray(paths);
    }

    private static Dictionary<AnimeStudio.Object, string> BuildContainerMap(
        IReadOnlyList<SerializedFile> files)
    {
        var result = new Dictionary<AnimeStudio.Object, string>();
        foreach (var file in files)
        {
            foreach (var bundle in file.Objects.OfType<AssetBundle>())
            {
                foreach (var entry in bundle.m_Container)
                {
                    var end = entry.Value.preloadIndex + entry.Value.preloadSize;
                    for (var index = entry.Value.preloadIndex; index < end; index++)
                    {
                        if (bundle.m_PreloadTable[index].TryGet(out var asset))
                        {
                            result[asset] = ResolveContainer(entry.Key);
                        }
                    }
                }
            }
            foreach (var resourceManager in file.Objects.OfType<ResourceManager>())
            {
                foreach (var entry in resourceManager.m_Container)
                {
                    if (entry.Value.TryGet(out var asset))
                    {
                        result[asset] = ResolveContainer(entry.Key);
                    }
                }
            }
        }
        return result;
    }

    private static string ResolveContainer(string value) =>
        ulong.TryParse(value, out var hash) && AssetsHelper.Paths.TryGetValue(hash, out var path)
            ? path
            : value;

    private static IReadOnlyDictionary<SerializedFile, string> BindLoadedFiles(
        IReadOnlyList<SerializedFile> files,
        IReadOnlyList<(string InputId, string InputPath)> inputs,
        CabMapDocument cabMap)
    {
        var inputByPath = inputs.ToDictionary(
            value => value.InputPath,
            value => value.InputId,
            StringComparer.OrdinalIgnoreCase);
        var cabByName = cabMap.Entries.ToDictionary(
            value => value.Cab,
            StringComparer.OrdinalIgnoreCase);
        var result = new Dictionary<SerializedFile, string>();
        foreach (var file in files)
        {
            var originalPath = Path.GetFullPath(file.originalPath ?? file.fullName);
            if (!inputByPath.TryGetValue(originalPath, out var physicalInputId))
            {
                throw new MonoBehaviourExportException(
                    "implicit_dependency",
                    $"加载器发现了未显式声明的输入：{file.fileName}");
            }
            if (!cabByName.TryGetValue(file.fileName, out var entry))
            {
                throw new MonoBehaviourExportException(
                    "cab_map_entry_missing",
                    $"CABMap 缺少已加载 SerializedFile：{file.fileName}");
            }
            if (!string.Equals(entry.InputId, physicalInputId, StringComparison.Ordinal))
            {
                throw new MonoBehaviourExportException(
                    "cab_map_input_mismatch",
                    $"CABMap 将 {file.fileName} 绑定到 {entry.InputId}，实际输入为 {physicalInputId}。");
            }
            if (entry.SerializedFileOffset != file.offset)
            {
                throw new MonoBehaviourExportException(
                    "cab_map_offset_mismatch",
                    $"CABMap 中 {file.fileName} 的 SerializedFileOffset 为 " +
                    $"{entry.SerializedFileOffset}，实际为 {file.offset}。");
            }
            result[file] = entry.InputId;
        }
        return result;
    }

    private static CabMapDocument LoadCabMap(string path, IEnumerable<string> inputIds)
    {
        var document = System.Text.Json.JsonSerializer.Deserialize<CabMapDocument>(
            File.ReadAllText(path),
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true })
            ?? throw new MonoBehaviourExportException("invalid_cab_map", "CABMap JSON 根节点无效。");
        if (document.SchemaVersion != 1 || document.Entries is null)
        {
            throw new MonoBehaviourExportException(
                "unsupported_cab_map",
                $"不支持 CABMap schemaVersion {document.SchemaVersion}。");
        }
        var allowed = inputIds.ToHashSet(StringComparer.Ordinal);
        var seenCabs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var seenInputIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var entry in document.Entries)
        {
            if (string.IsNullOrWhiteSpace(entry.Cab) ||
                !seenCabs.Add(entry.Cab) ||
                !allowed.Contains(entry.InputId))
            {
                throw new MonoBehaviourExportException(
                    "invalid_cab_map",
                    $"CABMap 包含空/重复 CAB 或未知 inputId：{entry.Cab} / {entry.InputId}");
            }
            seenInputIds.Add(entry.InputId);
        }
        if (!seenInputIds.SetEquals(allowed))
        {
            throw new MonoBehaviourExportException(
                "invalid_cab_map",
                "CABMap 没有覆盖本次请求的全部显式输入。");
        }
        return document;
    }

    private static string StableSourceDirectory(string sourceFile)
    {
        if (string.IsNullOrWhiteSpace(sourceFile) ||
            !string.Equals(Path.GetFileName(sourceFile), sourceFile, StringComparison.Ordinal))
        {
            throw new MonoBehaviourExportException(
                "invalid_source_file",
                $"SerializedFile 名称不能安全用于产物路径：{sourceFile}");
        }
        return sourceFile;
    }

    private static void LoadExplicitInputs(
        AssetsManager manager,
        IReadOnlyList<(string InputId, string InputPath)> inputs,
        string primaryInputId)
    {
        var orderedInputPaths = inputs
            .OrderByDescending(value => string.Equals(
                value.InputId,
                primaryInputId,
                StringComparison.Ordinal))
            .Select(value => value.InputPath)
            .ToArray();
        manager.LoadFiles(orderedInputPaths);
    }

    private static IReadOnlyList<IdentifiedTextureSelection> PrepareTextureSelections(
        IReadOnlyList<IdentifiedTextureSelection>? values)
    {
        if (values is null || values.Count == 0)
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "纹理导出必须至少包含一个精确 selection。");
        }
        var identities = new HashSet<(string SourceFile, long PathId)>(
            SourcePathIdComparer.Instance);
        var result = new List<IdentifiedTextureSelection>(values.Count);
        foreach (var value in values)
        {
            var sourceFile = value.SourceFile?.Trim();
            if (string.IsNullOrEmpty(sourceFile) ||
                value.PathId == 0 ||
                !identities.Add((sourceFile, value.PathId)))
            {
                throw new MonoBehaviourExportException(
                    "invalid_texture_selection",
                    $"纹理 selection 为空、重复或 PathID 为零：{value.SourceFile} / {value.PathId}");
            }
            result.Add(new IdentifiedTextureSelection(sourceFile, value.PathId));
        }
        return result;
    }

    private static string SafeObjectName(string value)
    {
        var name = string.IsNullOrWhiteSpace(value) ? "Texture2D" : value;
        foreach (var character in Path.GetInvalidFileNameChars())
        {
            name = name.Replace(character, '_');
        }
        return name.Length < 180 ? name : name[..180];
    }

    private sealed class SourcePathIdComparer : IEqualityComparer<(string SourceFile, long PathId)>
    {
        public static readonly SourcePathIdComparer Instance = new();

        public bool Equals(
            (string SourceFile, long PathId) left,
            (string SourceFile, long PathId) right) =>
            left.PathId == right.PathId &&
            string.Equals(left.SourceFile, right.SourceFile, StringComparison.OrdinalIgnoreCase);

        public int GetHashCode((string SourceFile, long PathId) value) =>
            HashCode.Combine(
                StringComparer.OrdinalIgnoreCase.GetHashCode(value.SourceFile),
                value.PathId);
    }

    private static IReadOnlyList<(string InputId, string InputPath)> PrepareInputs(
        IReadOnlyList<ObjectSnapshotInput>? values)
    {
        if (values is null || values.Count == 0)
        {
            throw new MonoBehaviourExportException("invalid_input", "对象快照必须至少包含一个输入。");
        }
        var ids = new HashSet<string>(StringComparer.Ordinal);
        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var result = new List<(string InputId, string InputPath)>();
        foreach (var value in values)
        {
            var id = value.InputId?.Trim();
            var path = ExportRequestGuard.PrepareInput(value.InputPath);
            if (string.IsNullOrEmpty(id) || !ids.Add(id))
            {
                throw new MonoBehaviourExportException(
                    "duplicate_input_id",
                    $"对象快照 inputId 为空或重复：{value.InputId}");
            }
            if (!paths.Add(path))
            {
                throw new MonoBehaviourExportException("duplicate_input_path", $"对象快照输入路径重复：{path}");
            }
            result.Add((id, path));
        }
        return result;
    }

    private static HashSet<ClassIDType> ParseIncludedTypes(IReadOnlyList<string>? names)
    {
        if (names is null || names.Count == 0)
        {
            throw new MonoBehaviourExportException("invalid_input", "对象快照必须显式声明 includedTypes。");
        }
        var result = new HashSet<ClassIDType>();
        foreach (var name in names)
        {
            if (!Enum.TryParse(name, true, out ClassIDType type) ||
                !SupportedTypes.Contains(type) || !result.Add(type))
            {
                throw new MonoBehaviourExportException("invalid_asset_type", $"对象快照包含未知、重复或尚未支持的类型：{name}");
            }
        }
        return result;
    }

    private static HashSet<string> ParseSelectionInputIds(
        IReadOnlyList<string>? values,
        IEnumerable<string> availableInputIds)
    {
        if (values is null || values.Count == 0)
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "对象快照必须显式声明 selectionInputIds。");
        }
        var available = availableInputIds.ToHashSet(StringComparer.Ordinal);
        var result = new HashSet<string>(StringComparer.Ordinal);
        foreach (var value in values)
        {
            var inputId = value?.Trim();
            if (string.IsNullOrEmpty(inputId) ||
                !available.Contains(inputId) ||
                !result.Add(inputId))
            {
                throw new MonoBehaviourExportException(
                    "invalid_selection_input",
                    $"对象快照 selectionInputId 为空、重复或未知：{value}");
            }
        }
        return result;
    }

    private static HashSet<string> ParseContainers(IReadOnlyList<string>? values)
    {
        var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var value in values ?? [])
        {
            var normalized = value?.Replace('\\', '/').Trim();
            if (string.IsNullOrEmpty(normalized) || !result.Add(normalized))
            {
                throw new MonoBehaviourExportException("invalid_container", $"对象快照 container 为空或重复：{value}");
            }
        }
        return result;
    }
}
