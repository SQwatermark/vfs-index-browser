using AnimeStudio;

namespace Vfs.Endfield.Extensions;

/// <summary>
/// 终末地 AssetMap 所需解析能力。来源是锁定上游 CLI 的 App.config；输出筛选仍由请求显式
/// 指定，解析依赖不能跟着输出类型一起被错误裁掉。
/// </summary>
internal static class EndfieldAssetTypeProfile
{
    public static void Configure()
    {
        var types = new Dictionary<ClassIDType, (bool Parse, bool Export)>();
        foreach (var type in ParseOnly)
        {
            types[type] = (true, false);
        }
        foreach (var type in ParseAndExport)
        {
            types[type] = (true, true);
        }
        TypeFlags.SetTypes(types);
    }

    private static readonly ClassIDType[] ParseOnly =
    [
        ClassIDType.Animation,
        ClassIDType.AnimatorController,
        ClassIDType.AnimatorOverrideController,
        ClassIDType.AssetBundle,
        ClassIDType.Avatar,
        ClassIDType.GameObject,
        ClassIDType.IndexObject,
        ClassIDType.MeshFilter,
        ClassIDType.MeshRenderer,
        ClassIDType.MonoScript,
        ClassIDType.PlayerSettings,
        ClassIDType.RectTransform,
        ClassIDType.ResourceManager,
        ClassIDType.SkinnedMeshRenderer,
        ClassIDType.SpriteAtlas,
        ClassIDType.Transform,
    ];

    private static readonly ClassIDType[] ParseAndExport =
    [
        ClassIDType.AnimationClip,
        ClassIDType.Animator,
        ClassIDType.AudioClip,
        ClassIDType.Cubemap,
        ClassIDType.Font,
        ClassIDType.Material,
        ClassIDType.Mesh,
        ClassIDType.MiHoYoBinData,
        ClassIDType.MonoBehaviour,
        ClassIDType.MovieTexture,
        ClassIDType.Shader,
        ClassIDType.Sprite,
        ClassIDType.TextAsset,
        ClassIDType.Texture2D,
        ClassIDType.VideoClip,
    ];
}
