using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine;

public static class HumanoidBakeOracle
{
    [Serializable] private sealed class Input
    {
        public string name;
        public float sampleRate;
        public float[] times;
        public Node[] nodes;
        public HumanMapping[] humanBones;
        public PoseNode[] avatarPose;
        public FloatCurve[] curves;
        public bool applyFootIK;
        public bool applyPlayableIK;
        public bool sampleAllBones;
        public AvatarSettings avatar;
        public SyntheticPose[] syntheticPoses;
    }

    [Serializable] private sealed class Node
    {
        public string id;
        public string name;
        public string parentId;
        public float[] translation;
        public float[] rotation;
        public float[] scale;
    }

    [Serializable] private sealed class HumanMapping
    {
        public string boneName;
        public string humanName;
        public bool useDefaultValues;
        public float[] min;
        public float[] max;
        public float[] center;
        public float axisLength;
    }

    [Serializable] private sealed class PoseNode
    {
        public string name;
        public string parentName;
        public float[] translation;
        public float[] rotation;
        public float[] scale;
    }

    [Serializable] private sealed class FloatCurve
    {
        public string name;
        public float[] values;
    }

    [Serializable] private sealed class AvatarSettings
    {
        public float armTwist;
        public float foreArmTwist;
        public float upperLegTwist;
        public float legTwist;
        public float armStretch;
        public float legStretch;
        public float feetSpacing;
        public float humanScale;
        public bool hasTranslationDoF;
    }

    [Serializable] private sealed class SyntheticPose
    {
        public string name;
        public float[] bodyPosition;
        public float[] bodyRotation;
        public bool hasMuscle;
        public int muscleIndex;
        public float muscleValue;
    }

    [Serializable] private sealed class Output
    {
        public string format = "UnityHumanoidBakeOracle";
        public string unityVersion = Application.unityVersion;
        public string evaluationMode;
        public bool applyFootIK;
        public bool applyPlayableIK;
        public string name;
        public float sampleRate;
        public float humanScale;
        public float[] times;
        public string[] sampleNames;
        public BoneTrack[] tracks;
        public Vector3[] bodyPositions;
        public Quaternion[] bodyRotations;
        public AvatarReferential[] generatedReferentials;
        public string[] curveNames;
    }

    [Serializable] private sealed class AvatarReferential
    {
        public string boneName;
        public string humanName;
        public Quaternion preRotation;
        public Quaternion postRotation;
        public Vector3 limitSign;
    }

    [Serializable] private sealed class BoneTrack
    {
        public string id;
        public string name;
        public Vector3[] translations;
        public Quaternion[] rotations;
        public Vector3[] scales;
    }

    public static void Run()
    {
        string inputPath = GetArgument("-oracleInput");
        string outputPath = GetArgument("-oracleOutput");
        if (string.IsNullOrEmpty(inputPath) || string.IsNullOrEmpty(outputPath))
            throw new ArgumentException("-oracleInput and -oracleOutput are required");

        Input input = JsonUtility.FromJson<Input>(File.ReadAllText(inputPath));
        GameObject container = new GameObject("OracleRoot");
        try
        {
            Dictionary<string, Transform> transforms = BuildHierarchy(container.transform, input.nodes);
            Transform avatarRoot = FindAvatarRoot(transforms, input.humanBones);
            ApplyAvatarPose(transforms, input.avatarPose);
            Avatar avatar = AvatarBuilder.BuildHumanAvatar(
                avatarRoot.gameObject,
                BuildHumanDescription(input, avatarRoot)
            );
            if (!avatar.isValid || !avatar.isHuman)
                throw new InvalidOperationException("Unity rejected the reconstructed Humanoid Avatar");

            RestoreModelPose(transforms, input.nodes);
            Output output = input.syntheticPoses != null && input.syntheticPoses.Length > 0
                ? EvaluateSyntheticPoses(input, avatar, avatarRoot, transforms)
                : Bake(input, avatar, avatarRoot, transforms);
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath)) ?? ".");
            File.WriteAllText(outputPath, JsonUtility.ToJson(output, true));
            Debug.Log($"Humanoid oracle wrote {output.tracks.Length} tracks to {outputPath}");
        }
        finally
        {
            UnityEngine.Object.DestroyImmediate(container);
        }
    }

    private static Dictionary<string, Transform> BuildHierarchy(Transform container, Node[] nodes)
    {
        Dictionary<string, Transform> result = new Dictionary<string, Transform>();
        foreach (Node node in nodes)
        {
            GameObject gameObject = new GameObject(node.name);
            Transform transform = gameObject.transform;
            transform.localPosition = Vector(node.translation);
            transform.localRotation = QuaternionValue(node.rotation);
            transform.localScale = Vector(node.scale);
            result.Add(node.id, transform);
        }
        foreach (Node node in nodes)
        {
            Transform parent = !string.IsNullOrEmpty(node.parentId) && result.TryGetValue(node.parentId, out Transform value)
                ? value
                : container;
            result[node.id].SetParent(parent, false);
        }
        return result;
    }

    private static void ApplyAvatarPose(
        Dictionary<string, Transform> transforms,
        PoseNode[] pose)
    {
        foreach (PoseNode node in pose)
        {
            Transform transform = transforms.Values.Single(item => item.name == node.name);
            string actualParent = transform.parent != null ? transform.parent.name : "";
            if (!string.IsNullOrEmpty(node.parentName) && actualParent != node.parentName)
                throw new InvalidOperationException($"Avatar pose parent mismatch for {node.name}");
            transform.localPosition = Vector(node.translation);
            transform.localRotation = QuaternionValue(node.rotation);
            transform.localScale = Vector(node.scale);
        }
    }

    private static void RestoreModelPose(
        Dictionary<string, Transform> transforms,
        Node[] nodes)
    {
        foreach (Node node in nodes)
        {
            Transform transform = transforms[node.id];
            transform.localPosition = Vector(node.translation);
            transform.localRotation = QuaternionValue(node.rotation);
            transform.localScale = Vector(node.scale);
        }
    }

    private static Transform FindAvatarRoot(
        Dictionary<string, Transform> transforms,
        HumanMapping[] mappings)
    {
        HumanMapping hips = mappings.Single(mapping => Normalize(mapping.humanName) == "hips");
        Transform transform = transforms.Values.Single(item => item.name == hips.boneName);
        // AvatarBuilder expects the supplied object to own the complete humanoid hierarchy.
        while (transform.parent != null && transform.parent.name != "OracleRoot")
            transform = transform.parent;
        return transform;
    }

    private static HumanDescription BuildHumanDescription(
        Input input,
        Transform avatarRoot)
    {
        Dictionary<string, string> officialNames = HumanTrait.BoneName
            .ToDictionary(Normalize, name => name);
        HumanBone[] human = input.humanBones.Select(mapping =>
        {
            string normalized = Normalize(mapping.humanName);
            if (!officialNames.TryGetValue(normalized, out string humanName))
                throw new InvalidOperationException($"Unknown human bone {mapping.humanName}");
            return new HumanBone
            {
                boneName = mapping.boneName,
                humanName = humanName,
                limit = new HumanLimit
                {
                    useDefaultValues = mapping.useDefaultValues,
                    min = Vector(mapping.min),
                    max = Vector(mapping.max),
                    center = Vector(mapping.center),
                    axisLength = mapping.axisLength,
                },
            };
        }).ToArray();

        SkeletonBone[] skeleton = avatarRoot.GetComponentsInChildren<Transform>(true)
            .Select(transform => new SkeletonBone
            {
                name = transform.name,
                position = transform.localPosition,
                rotation = transform.localRotation,
                scale = transform.localScale,
            })
            .ToArray();
        return new HumanDescription
        {
            human = human,
            skeleton = skeleton,
            upperArmTwist = input.avatar.armTwist,
            lowerArmTwist = input.avatar.foreArmTwist,
            upperLegTwist = input.avatar.upperLegTwist,
            lowerLegTwist = input.avatar.legTwist,
            armStretch = input.avatar.armStretch,
            legStretch = input.avatar.legStretch,
            feetSpacing = input.avatar.feetSpacing,
            hasTranslationDoF = input.avatar.hasTranslationDoF,
        };
    }

    private static Output Bake(
        Input input,
        Avatar avatar,
        Transform avatarRoot,
        Dictionary<string, Transform> transforms)
    {
        Transform[] animatedBones = GetAnimatedBones(input, avatarRoot, transforms);
        Dictionary<Transform, BoneTrack> tracks = CreateTracks(
            animatedBones,
            transforms,
            input.times.Length
        );
        Vector3[] bodyPositions = new Vector3[input.times.Length];
        Quaternion[] bodyRotations = new Quaternion[input.times.Length];

        Animator animator = avatarRoot.gameObject.AddComponent<Animator>();
        animator.avatar = avatar;
        animator.applyRootMotion = false;
        animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
        AnimationClip clip = BuildAnimationClip(input);
        PlayableGraph graph = PlayableGraph.Create("HumanoidBakeOracle");
        HumanPoseHandler poseHandler = new HumanPoseHandler(avatar, avatarRoot);
        try
        {
            AnimationClipPlayable playable = AnimationClipPlayable.Create(graph, clip);
            playable.SetApplyFootIK(input.applyFootIK);
            playable.SetApplyPlayableIK(input.applyPlayableIK);
            playable.SetSpeed(0);
            AnimationPlayableOutput output = AnimationPlayableOutput.Create(graph, "Animation", animator);
            output.SetSourcePlayable(playable);
            graph.Play();
            for (int frame = 0; frame < input.times.Length; frame++)
            {
                playable.SetTime(input.times[frame]);
                graph.Evaluate(0);
                HumanPose pose = new HumanPose();
                poseHandler.GetHumanPose(ref pose);
                bodyPositions[frame] = pose.bodyPosition;
                bodyRotations[frame] = pose.bodyRotation;
                CaptureTracks(tracks, animatedBones, frame);
            }
        }
        finally
        {
            poseHandler.Dispose();
            graph.Destroy();
            UnityEngine.Object.DestroyImmediate(clip);
            UnityEngine.Object.DestroyImmediate(animator);
        }
        return new Output
        {
            evaluationMode = "AnimatorPlayable",
            name = input.name,
            sampleRate = input.sampleRate,
            humanScale = input.avatar.humanScale,
            times = input.times,
            sampleNames = Array.Empty<string>(),
            applyFootIK = input.applyFootIK,
            applyPlayableIK = input.applyPlayableIK,
            tracks = tracks.Values.ToArray(),
            bodyPositions = bodyPositions,
            bodyRotations = bodyRotations,
            generatedReferentials = ReadGeneratedReferentials(avatar, input.humanBones),
            curveNames = input.curves.Select(curve => curve.name).ToArray(),
        };
    }

    private static Output EvaluateSyntheticPoses(
        Input input,
        Avatar avatar,
        Transform avatarRoot,
        Dictionary<string, Transform> transforms)
    {
        Transform[] animatedBones = GetAnimatedBones(input, avatarRoot, transforms);
        int sampleCount = input.syntheticPoses.Length;
        Dictionary<Transform, BoneTrack> tracks = CreateTracks(animatedBones, transforms, sampleCount);
        Vector3[] bodyPositions = new Vector3[sampleCount];
        Quaternion[] bodyRotations = new Quaternion[sampleCount];
        HumanPoseHandler poseHandler = new HumanPoseHandler(avatar, avatarRoot);
        try
        {
            for (int sampleIndex = 0; sampleIndex < sampleCount; sampleIndex++)
            {
                SyntheticPose source = input.syntheticPoses[sampleIndex];
                if (source.bodyPosition == null || source.bodyPosition.Length != 3)
                    throw new InvalidOperationException($"Invalid Body position in {source.name}");
                if (source.bodyRotation == null || source.bodyRotation.Length != 4)
                    throw new InvalidOperationException($"Invalid Body rotation in {source.name}");
                float[] muscles = new float[HumanTrait.MuscleCount];
                if (source.hasMuscle)
                {
                    if (source.muscleIndex < 0 || source.muscleIndex >= muscles.Length)
                        throw new InvalidOperationException($"Invalid muscle index {source.muscleIndex}");
                    muscles[source.muscleIndex] = source.muscleValue;
                }
                HumanPose pose = new HumanPose
                {
                    bodyPosition = Vector(source.bodyPosition),
                    bodyRotation = QuaternionValue(source.bodyRotation),
                    muscles = muscles,
                };
                poseHandler.SetHumanPose(ref pose);
                poseHandler.GetHumanPose(ref pose);
                bodyPositions[sampleIndex] = pose.bodyPosition;
                bodyRotations[sampleIndex] = pose.bodyRotation;
                CaptureTracks(tracks, animatedBones, sampleIndex);
            }
        }
        finally
        {
            poseHandler.Dispose();
        }
        return new Output
        {
            evaluationMode = "SyntheticHumanPose",
            name = input.name,
            sampleRate = 0,
            humanScale = input.avatar.humanScale,
            times = Enumerable.Range(0, sampleCount).Select(index => (float)index).ToArray(),
            sampleNames = input.syntheticPoses.Select(pose => pose.name).ToArray(),
            tracks = tracks.Values.ToArray(),
            bodyPositions = bodyPositions,
            bodyRotations = bodyRotations,
            generatedReferentials = ReadGeneratedReferentials(avatar, input.humanBones),
            curveNames = HumanTrait.MuscleName,
        };
    }

    private static Transform[] GetAnimatedBones(
        Input input,
        Transform avatarRoot,
        Dictionary<string, Transform> transforms) =>
        input.sampleAllBones
            ? avatarRoot.GetComponentsInChildren<Transform>(true)
            : input.humanBones
                .Select(mapping => transforms.Values.Single(item => item.name == mapping.boneName))
                .Distinct()
                .ToArray();

    private static Dictionary<Transform, BoneTrack> CreateTracks(
        Transform[] animatedBones,
        Dictionary<string, Transform> transforms,
        int sampleCount) =>
        animatedBones.ToDictionary(
            transform => transform,
            transform => new BoneTrack
            {
                id = transforms.Single(pair => pair.Value == transform).Key,
                name = transform.name,
                translations = new Vector3[sampleCount],
                rotations = new Quaternion[sampleCount],
                scales = new Vector3[sampleCount],
            });

    private static void CaptureTracks(
        Dictionary<Transform, BoneTrack> tracks,
        Transform[] animatedBones,
        int sampleIndex)
    {
        foreach (Transform bone in animatedBones)
        {
            tracks[bone].translations[sampleIndex] = bone.localPosition;
            tracks[bone].rotations[sampleIndex] = bone.localRotation;
            tracks[bone].scales[sampleIndex] = bone.localScale;
        }
    }

    private static AnimationClip BuildAnimationClip(Input input)
    {
        AnimationClip clip = new AnimationClip
        {
            name = input.name,
            frameRate = input.sampleRate,
            legacy = false,
        };
        foreach (FloatCurve source in input.curves)
        {
            if (source.values.Length != input.times.Length)
                throw new InvalidOperationException($"Curve length mismatch for {source.name}");
            Keyframe[] keys = new Keyframe[input.times.Length];
            for (int index = 0; index < keys.Length; index++)
                keys[index] = new Keyframe(input.times[index], source.values[index]);
            clip.SetCurve(string.Empty, typeof(Animator), source.name, new AnimationCurve(keys));
        }
        clip.EnsureQuaternionContinuity();
        return clip;
    }

    private static AvatarReferential[] ReadGeneratedReferentials(
        Avatar avatar,
        HumanMapping[] mappings)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        MethodInfo getPreRotation = typeof(Avatar).GetMethod("GetPreRotation", flags);
        MethodInfo getPostRotation = typeof(Avatar).GetMethod("GetPostRotation", flags);
        MethodInfo getLimitSign = typeof(Avatar).GetMethod("GetLimitSign", flags);
        if (getPreRotation == null || getPostRotation == null || getLimitSign == null)
            throw new MissingMethodException("Unity Avatar referential accessors are unavailable");

        Dictionary<string, int> boneIndices = HumanTrait.BoneName
            .Select((name, index) => new { name, index })
            .ToDictionary(item => Normalize(item.name), item => item.index);
        return mappings.Select(mapping =>
        {
            int boneIndex = boneIndices[Normalize(mapping.humanName)];
            HumanBodyBones bone = (HumanBodyBones)boneIndex;
            return new AvatarReferential
            {
                boneName = mapping.boneName,
                humanName = HumanTrait.BoneName[boneIndex],
                preRotation = (Quaternion)getPreRotation.Invoke(avatar, new object[] { bone }),
                postRotation = (Quaternion)getPostRotation.Invoke(avatar, new object[] { bone }),
                limitSign = (Vector3)getLimitSign.Invoke(avatar, new object[] { bone }),
            };
        }).ToArray();
    }

    private static string GetArgument(string name)
    {
        string[] args = Environment.GetCommandLineArgs();
        int index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private static string Normalize(string value) =>
        new string(value.Where(char.IsLetterOrDigit).Select(char.ToLowerInvariant).ToArray());

    private static Vector3 Vector(float[] values) => new Vector3(values[0], values[1], values[2]);
    private static Quaternion QuaternionValue(float[] values) =>
        new Quaternion(values[0], values[1], values[2], values[3]);
}
