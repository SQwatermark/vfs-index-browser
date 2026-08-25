using Newtonsoft.Json;
using Newtonsoft.Json.Converters;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;

namespace AnimeStudio.CLI
{
    /// <summary>
    /// Builds a versioned object snapshot for consumers that need stable Unity
    /// identities and resolved PPtr references in addition to the JSON payload.
    /// </summary>
    internal static class ObjectSnapshotExporter
    {
        public const string Contract = "AnimeStudioObjectSnapshot";
        public const string ContractVersion = "1.0.0";

        public static JObject Build(AssetItem item)
        {
            var serializer = JsonSerializer.Create(new JsonSerializerSettings
            {
                Converters = { new StringEnumConverter() },
            });
            var payload = JObject.FromObject(BuildSnapshotValue(item.Asset), serializer);
            var rawData = item.Asset.GetRawData();
            var metadata = new JObject
            {
                ["contract"] = Contract,
                ["version"] = ContractVersion,
                ["pathId"] = item.m_PathID,
                ["type"] = item.TypeString,
                ["classId"] = (int)item.Type,
                ["name"] = item.Text,
                ["sourceFile"] = item.SourceFile.fileName,
                ["sourceOriginalPath"] = item.SourceFile.originalPath ?? string.Empty,
                ["container"] = item.Container ?? string.Empty,
                ["byteSize"] = item.FullSize,
                ["rawDataLength"] = rawData.Length,
                ["rawDataSha256"] = Convert.ToHexString(SHA256.HashData(rawData)).ToLowerInvariant(),
            };

            AddTypeTreeMetadata(metadata, item.Asset.serializedType?.m_Type);
            metadata["externalFiles"] = BuildExternalFiles(item.SourceFile);
            metadata["pptrReferences"] = BuildPPtrReferences(payload, item.SourceFile);
            payload.AddFirst(new JProperty("$animestudio", metadata));
            return payload;
        }

        private static object BuildSnapshotValue(AnimeStudio.Object asset)
        {
            if (asset is Animator)
            {
                // 终末地在 Animator 中扩展了骨骼约束字段，CLR 模型不会暴露这些内容。
                // 快照必须按资源自带的类型树读取，才能保留完整的约束配置。
                var animatorValue = asset.ToType();
                return animatorValue != null ? animatorValue : asset;
            }

            if (asset is not MonoBehaviour monoBehaviour)
            {
                return asset;
            }

            // MonoBehaviour 的 CLR 类型只包含 Unity 基类字段；自定义序列化字段
            // 必须通过类型树读取，否则快照会悄悄丢失组件的实际配置。
            var value = monoBehaviour.ToType();
            if (value != null)
            {
                return value;
            }

            var typeTree = Studio.MonoBehaviourToTypeTree(monoBehaviour);
            var fallback = monoBehaviour.ToType(typeTree);
            return fallback != null ? fallback : asset;
        }

        private static JArray BuildExternalFiles(SerializedFile owner)
        {
            var files = new JArray();
            for (var index = 0; index < owner.m_Externals.Count; index++)
            {
                var external = owner.m_Externals[index];
                var file = new JObject
                {
                    ["fileId"] = index + 1,
                    ["guid"] = external.guid.ToString("D"),
                    ["type"] = external.type,
                    ["pathName"] = external.pathName ?? string.Empty,
                    ["fileName"] = external.fileName ?? string.Empty,
                };
                var loaded = owner.assetsManager.assetsFileList.FirstOrDefault(
                    candidate => candidate.fileName.Equals(
                        external.fileName,
                        StringComparison.OrdinalIgnoreCase));
                file["loaded"] = loaded != null;
                if (loaded != null)
                {
                    file["loadedSourceOriginalPath"] = loaded.originalPath ?? string.Empty;
                }
                files.Add(file);
            }
            return files;
        }

        private static void AddTypeTreeMetadata(JObject metadata, TypeTree typeTree)
        {
            if (typeTree?.m_Nodes == null)
            {
                metadata["typeTreeSource"] = "none";
                metadata["typeTreeNodeCount"] = 0;
                metadata["typeTreeFieldPaths"] = new JArray();
                return;
            }

            metadata["typeTreeSource"] = "serializedType";
            metadata["typeTreeNodeCount"] = typeTree.m_Nodes.Count;
            metadata["typeTreeFieldPaths"] = new JArray(BuildTypeTreeFieldPaths(typeTree.m_Nodes));
        }

        internal static IEnumerable<string> BuildTypeTreeFieldPaths(
            IReadOnlyList<TypeTreeNode> nodes)
        {
            var path = new List<string>();
            foreach (var node in nodes.Skip(1))
            {
                var depth = Math.Max(0, node.m_Level - 1);
                while (path.Count > depth)
                {
                    path.RemoveAt(path.Count - 1);
                }
                path.Add(node.m_Name);
                yield return $"{string.Join(".", path)}:{node.m_Type}";
            }
        }

        private static JArray BuildPPtrReferences(JObject payload, SerializedFile owner)
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
                    ["path"] = ToJsonPath(candidate.Path),
                    ["fileId"] = fileId,
                    ["pathId"] = pathId,
                };
                if (TryResolveTarget(owner, fileId, pathId, out var target))
                {
                    reference["targetType"] = target.type.ToString();
                    reference["targetPathId"] = target.m_PathID;
                    reference["targetName"] = target.Name;
                    reference["targetSourceFile"] = target.assetsFile.fileName;
                    reference["targetSourceOriginalPath"] =
                        target.assetsFile.originalPath ?? string.Empty;
                }
                references.Add(reference);
            }
            return references;
        }

        private static bool TryReadPPtr(JObject candidate, out int fileId, out long pathId)
        {
            fileId = 0;
            pathId = 0;
            var fileIdToken = candidate["m_FileID"];
            var pathIdToken = candidate["m_PathID"];
            return fileIdToken?.Type == JTokenType.Integer &&
                pathIdToken?.Type == JTokenType.Integer &&
                int.TryParse(fileIdToken.ToString(), out fileId) &&
                long.TryParse(pathIdToken.ToString(), out pathId);
        }

        private static bool TryResolveTarget(
            SerializedFile owner,
            int fileId,
            long pathId,
            out AnimeStudio.Object target)
        {
            target = null;
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
                    file => file.fileName.Equals(
                        externalName,
                        StringComparison.OrdinalIgnoreCase));
            }
            return source != null && source.ObjectsDic.TryGetValue(pathId, out target);
        }

        private static string ToJsonPath(string path) =>
            string.IsNullOrEmpty(path) ? "$" : $"$.{path}";
    }
}
