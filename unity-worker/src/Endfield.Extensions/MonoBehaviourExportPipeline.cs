using AnimeStudio;
using UnityObject = AnimeStudio.Object;

namespace Vfs.Endfield.Extensions;

public sealed class MonoBehaviourExportException : Exception
{
    public MonoBehaviourExportException(string code, string message)
        : base(message)
    {
        Code = code;
    }

    public string Code { get; }
}

/// <summary>
/// MonoBehaviour 各种导出模式共用的定位层。它只处理输入校验、Bundle 加载、container
/// 映射与对象选择，不决定对象最终被解释成 Raw、TypeTree 还是领域数据。
/// </summary>
internal static class MonoBehaviourExportPipeline
{
    public static T Run<T>(
        string inputPathValue,
        string outputDirectoryValue,
        string? requestedContainer,
        Func<string, IReadOnlyList<SelectedMonoBehaviour>, T> export)
    {
        var (inputPath, outputDirectory) = ExportRequestGuard.Prepare(
            inputPathValue,
            outputDirectoryValue);
        var game = GameManager.GetGame(GameType.ArknightsEndfield)
            ?? throw new MonoBehaviourExportException(
                "game_profile_missing",
                "AnimeStudio 核心未提供 ArknightsEndfield 游戏配置。");

        // 这是所有 MonoBehaviour 导出模式的最小公共解析集合。
        TypeFlags.SetTypes(new Dictionary<ClassIDType, (bool Parse, bool Export)>
        {
            [ClassIDType.AssetBundle] = (true, false),
            [ClassIDType.MonoBehaviour] = (true, true),
            [ClassIDType.MonoScript] = (true, false),
        });

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
            var selected = SelectMonoBehaviours(
                manager.assetsFileList,
                containers,
                requestedContainer);
            if (selected.Count == 0)
            {
                throw new MonoBehaviourExportException(
                    "mono_behaviour_not_found",
                    requestedContainer is null
                        ? "Bundle 中没有 MonoBehaviour 对象。"
                        : $"Bundle 中没有精确匹配 container 的 MonoBehaviour：{requestedContainer}");
            }
            return export(outputDirectory, selected);
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
                        // 与旧 CLI 一致：同一对象被多个 container 引用时，后出现的关联覆盖前者。
                        containers[asset] = entry.Key;
                    }
                }
            }
        }
        return containers;
    }

    private static List<SelectedMonoBehaviour> SelectMonoBehaviours(
        IReadOnlyList<SerializedFile> files,
        IReadOnlyDictionary<UnityObject, string> containers,
        string? requestedContainer)
    {
        var selected = new List<SelectedMonoBehaviour>();
        for (var sourceOrdinal = 0; sourceOrdinal < files.Count; sourceOrdinal++)
        {
            foreach (var asset in files[sourceOrdinal].Objects.OfType<MonoBehaviour>())
            {
                containers.TryGetValue(asset, out var container);
                if (requestedContainer is not null &&
                    !string.Equals(container, requestedContainer, StringComparison.Ordinal))
                {
                    continue;
                }
                selected.Add(new SelectedMonoBehaviour(sourceOrdinal, asset, container));
            }
        }
        return selected;
    }
}

internal sealed record SelectedMonoBehaviour(
    int SourceOrdinal,
    MonoBehaviour Asset,
    string? Container);
