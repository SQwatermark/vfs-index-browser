using System.Security.Cryptography;
using AnimeStudio;
using Newtonsoft.Json;
using SevenZip;

namespace Vfs.Endfield.Extensions;

public sealed record AnimationClipJsonExportRequest(
    string InputPath,
    string OutputDirectory,
    long PathId,
    string ExpectedName);

public sealed record AnimationClipJsonArtifact(
    string RelativePath,
    string SourceFile,
    long PathId,
    string Name,
    int CurveCount,
    int TimelineCount,
    double Duration,
    long ByteCount,
    string Sha256);

public sealed record AnimationClipJsonExportResult(
    int ArtifactCount,
    IReadOnlyList<AnimationClipJsonArtifact> Artifacts);

/// <summary>
/// 将一个精确 AnimationClip 解码为 VFS 已消费的 AnimeStudioAnimationClip 契约。
/// 选择边界是 PathID + 名称；不暴露 CLI 的类型扫描和输出命名规则。
/// </summary>
public static class AnimationClipJsonExporter
{
    public static AnimationClipJsonExportResult Export(
        AnimationClipJsonExportRequest request)
    {
        var expectedName = request.ExpectedName?.Trim();
        if (string.IsNullOrEmpty(expectedName))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "动画导出必须提供 expectedName。");
        }
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
            var matches = manager.assetsFileList
                .SelectMany(file => file.Objects.OfType<AnimationClip>())
                .Where(clip =>
                    clip.m_PathID == request.PathId &&
                    string.Equals(clip.m_Name, expectedName, StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new MonoBehaviourExportException(
                    "animation_clip_not_found",
                    $"PathID + 名称应匹配一个 AnimationClip，实际为 {matches.Length}：" +
                    $"{request.PathId} / {expectedName}");
            }

            var clip = matches[0];
            var converter = AnimationClipConverter.Process(clip);
            if (converter.Eulers.Count != 0 || converter.PPtrs.Count != 0)
            {
                throw new MonoBehaviourExportException(
                    "unsupported_animation_curve",
                    "AnimationJSON 尚不支持 Euler 或对象引用曲线。");
            }

            var timelines = new List<float[]>();
            var timelineIndexes = new Dictionary<string, int>();
            int AddTimeline(IEnumerable<float> values)
            {
                var timeline = values.ToArray();
                var key = string.Join(",", timeline.Select(BitConverter.SingleToInt32Bits));
                if (timelineIndexes.TryGetValue(key, out var existing))
                {
                    return existing;
                }
                var index = timelines.Count;
                timelines.Add(timeline);
                timelineIndexes.Add(key, index);
                return index;
            }

            var curves = new List<object>();
            curves.AddRange(converter.Translations.Select(curve => new
            {
                path = curve.path,
                pathHash = GetPathHash(curve.path),
                property = "translation",
                timeline = AddTimeline(curve.curve.m_Curve.Select(key => key.time)),
                values = curve.curve.m_Curve.Select(
                    key => new[] { key.value.X, key.value.Y, key.value.Z }),
            }));
            curves.AddRange(converter.Rotations.Select(curve => new
            {
                path = curve.path,
                pathHash = GetPathHash(curve.path),
                property = "rotation",
                timeline = AddTimeline(curve.curve.m_Curve.Select(key => key.time)),
                values = curve.curve.m_Curve.Select(
                    key => new[] { key.value.X, key.value.Y, key.value.Z, key.value.W }),
            }));
            curves.AddRange(converter.Scales.Select(curve => new
            {
                path = curve.path,
                pathHash = GetPathHash(curve.path),
                property = "scale",
                timeline = AddTimeline(curve.curve.m_Curve.Select(key => key.time)),
                values = curve.curve.m_Curve.Select(
                    key => new[] { key.value.X, key.value.Y, key.value.Z }),
            }));
            curves.AddRange(converter.Floats.Select(curve => new
            {
                path = curve.path,
                pathHash = GetPathHash(curve.path),
                property = "float",
                propertyName = curve.attribute,
                classId = (int)curve.classID,
                timeline = AddTimeline(curve.curve.m_Curve.Select(key => key.time)),
                values = curve.curve.m_Curve.Select(key => key.value.Value),
            }));

            var duration = timelines
                .SelectMany(timeline => timeline)
                .DefaultIfEmpty(0.0f)
                .Max();
            var sourceBindings = clip.m_ClipBindingConstant.genericBindings
                .Select((binding, index) => new
                {
                    index,
                    pathHash = binding.path,
                    attribute = binding.attribute,
                    classId = (int)binding.typeID,
                    customType = binding.customType,
                    isPPtrCurve = binding.isPPtrCurve != 0,
                    isIntCurve = binding.isIntCurve != 0,
                    humanoidPropertyName =
                        (BindingCustomType)binding.customType == BindingCustomType.AnimatorMuscle
                            ? binding.GetHumanoidPropertyName(true)
                            : null,
                })
                .ToArray();
            var muscle = clip.m_MuscleClip;
            var acl = clip.m_AclCompressedBuffer;
            var document = new
            {
                format = "AnimeStudioAnimationClip",
                version = "1.1.0",
                name = clip.m_Name,
                sampleRate = clip.m_SampleRate,
                duration,
                timelines,
                curves,
                sourceBindings,
                humanoid = new
                {
                    muscleClipSize = clip.m_MuscleClipSize,
                    deltaPose = muscle.m_DeltaPose,
                    start = muscle.m_StartX,
                    stop = muscle.m_StopX,
                    leftFootStart = muscle.m_LeftFootStartX,
                    rightFootStart = muscle.m_RightFootStartX,
                    averageSpeed = muscle.m_AverageSpeed,
                    startTime = muscle.m_StartTime,
                    stopTime = muscle.m_StopTime,
                    orientationOffsetY = muscle.m_OrientationOffsetY,
                    level = muscle.m_Level,
                    cycleOffset = muscle.m_CycleOffset,
                    averageAngularSpeed = muscle.m_AverageAngularSpeed,
                    indexArray = muscle.m_IndexArray,
                    valueArrayDelta = muscle.m_ValueArrayDelta,
                    valueArrayReferencePose = muscle.m_ValueArrayReferencePose,
                    mirror = muscle.m_Mirror,
                    loopTime = muscle.m_LoopTime,
                    loopBlend = muscle.m_LoopBlend,
                    loopBlendOrientation = muscle.m_LoopBlendOrientation,
                    loopBlendPositionY = muscle.m_LoopBlendPositionY,
                    loopBlendPositionXZ = muscle.m_LoopBlendPositionXZ,
                    startAtOrigin = muscle.m_StartAtOrigin,
                    keepOriginalOrientation = muscle.m_KeepOriginalOrientation,
                    keepOriginalPositionY = muscle.m_KeepOriginalPositionY,
                    keepOriginalPositionXZ = muscle.m_KeepOriginalPositionXZ,
                    heightFromFeet = muscle.m_HeightFromFeet,
                    reducedDeltaValue = muscle.m_ReducedDeltaValue,
                },
                endfieldAcl = acl == null ? null : new
                {
                    version = acl.Version,
                    outputTrackCount = acl.OutputTrackCount,
                    rootPositionIndex = acl.RootPosIndex,
                    rootRotationIndex = acl.RootRotIndex,
                    rootScaleIndex = acl.RootScaleIndex,
                    rootTrackCount = acl.RootTrackCount,
                    floatCurveCount = acl.FloatCurveCount,
                    rootMotionBufferLength = acl.RootMotionBufferData?.Length ?? 0,
                    defaultIndices = acl.m_DefaultIndexs,
                    constantIndices = acl.m_ConstantIndexs,
                    constantValues = acl.m_ConstantValues,
                },
            };

            var relativePath = Path.Combine(
                "AnimationClip",
                StableSourceDirectory(clip.assetsFile.fileName),
                $"{SafeName(clip.m_Name)}_p{unchecked((ulong)clip.m_PathID):X16}.animation.json");
            var outputPath = Path.Combine(outputDirectory, relativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
            File.WriteAllText(
                outputPath,
                JsonConvert.SerializeObject(document, Formatting.None));
            var info = new FileInfo(outputPath);
            using var hashStream = File.OpenRead(outputPath);
            var artifact = new AnimationClipJsonArtifact(
                relativePath.Replace(Path.DirectorySeparatorChar, '/'),
                clip.assetsFile.fileName,
                clip.m_PathID,
                clip.m_Name,
                curves.Count,
                timelines.Count,
                duration,
                info.Length,
                Convert.ToHexString(SHA256.HashData(hashStream)).ToLowerInvariant());
            return new AnimationClipJsonExportResult(1, [artifact]);
        }
        finally
        {
            manager.Clear();
        }
    }

    private static uint GetPathHash(string path)
    {
        if (AnimationClipConverter.UnknownPathRegex.IsMatch(path))
        {
            return uint.Parse(path["path_".Length..]);
        }
        return CRC.CalculateDigestAscii(path);
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

    private static string SafeName(string value)
    {
        var name = string.IsNullOrWhiteSpace(value) ? "AnimationClip" : value;
        foreach (var character in Path.GetInvalidFileNameChars())
        {
            name = name.Replace(character, '_');
        }
        return name.Length < 150 ? name : name[..150];
    }
}
