using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Buffers.Binary;
using System.IO;
using System.Linq;

namespace AnimeStudio.CLI
{
    /// <summary>
    /// 无损导出终末地 Shader 的编译载荷。此处只拆分并校验 Unity 程序段，
    /// 不猜测终末地自定义 ShaderSubProgram 的内部结构。
    /// </summary>
    internal static class ShaderBinaryPackageExporter
    {
        private const string Format = "AnimeStudioEndfieldShaderBinaryPackage";
        private const string Version = "1.0.0";
        private const int EndfieldSubProgramVersion = 0x0C11FFE2;
        private const int EndfieldD3D11ProgramType = 33;

        internal static void Export(Shader shader, string outputRoot)
        {
            if (Directory.Exists(outputRoot))
            {
                Directory.Delete(outputRoot, true);
            }
            Directory.CreateDirectory(outputRoot);

            File.WriteAllBytes(Path.Combine(outputRoot, "shader-object.bin"), shader.GetRawData());

            var diagnostics = new JArray();
            var blobs = new JArray();
            if (!string.IsNullOrEmpty(shader.m_CompiledDataError))
            {
                AddDiagnostic(diagnostics, "shader-tail", shader.m_CompiledDataError);
            }

            if (shader.subShaderBlobs != null)
            {
                for (var index = 0; index < shader.subShaderBlobs.Count; index++)
                {
                    var blob = shader.subShaderBlobs[index];
                    ExportBlob(
                        outputRoot,
                        $"lod-{index:D3}",
                        "embedded-lod-blob",
                        blob.m_ShaderLOD,
                        blob.m_CompressedBlob,
                        blob.m_Offsets,
                        blob.m_CompressedLengths,
                        blob.m_DecompressedLengths,
                        blobs,
                        diagnostics);
                }
            }

            if (shader.m_SubShaderBinaryData != null)
            {
                for (var index = 0; index < shader.m_SubShaderBinaryData.Count; index++)
                {
                    var pointer = shader.m_SubShaderBinaryData[index];
                    var pointerInfo = new JObject
                    {
                        ["index"] = index,
                        ["fileId"] = pointer.m_FileID,
                        ["pathId"] = pointer.m_PathID,
                    };
                    if (!pointer.TryGet(out SubShaderBinaryData external))
                    {
                        pointerInfo["resolved"] = false;
                        blobs.Add(pointerInfo);
                        AddDiagnostic(
                            diagnostics,
                            $"external-{index:D3}",
                            $"Unable to resolve SubShaderBinaryData PPtr ({pointer.m_FileID}, {pointer.m_PathID}).");
                        continue;
                    }

                    pointerInfo["resolved"] = true;
                    pointerInfo["sourceFile"] = external.assetsFile.fileName;
                    pointerInfo["sourcePathId"] = external.m_PathID;
                    blobs.Add(pointerInfo);

                    var packedBytes = new byte[(external.m_CompressedBlob?.Length ?? 0) * sizeof(uint)];
                    if (packedBytes.Length > 0)
                    {
                        Buffer.BlockCopy(external.m_CompressedBlob, 0, packedBytes, 0, packedBytes.Length);
                    }
                    ExportBlob(
                        outputRoot,
                        $"external-{index:D3}",
                        "external-subshader-binary-data",
                        GetExternalLod(shader, index),
                        packedBytes,
                        external.m_Offsets,
                        external.m_CompressedLengths,
                        external.m_DecompressedLengths,
                        blobs,
                        diagnostics,
                        "uint32-little-endian");
                }
            }

            if (shader.compressedBlob != null &&
                (shader.compressedBlob.Length > 0 || HasSegments(shader.offsets)))
            {
                ExportBlob(
                    outputRoot,
                    "main",
                    "shader-main-blob",
                    null,
                    shader.compressedBlob,
                    shader.offsets,
                    shader.compressedLengths,
                    shader.decompressedLengths,
                    blobs,
                    diagnostics);
            }

            if (blobs.Count == 0)
            {
                AddDiagnostic(diagnostics, "shader", "No compiled Shader blob was parsed from this object.");
            }

            var structure = ExportShaderStructure(shader, diagnostics);
            var variantMappingEntries = ExportVariantMappings(
                structure,
                blobs,
                shader.platforms ?? Array.Empty<ShaderCompilerPlatform>(),
                diagnostics);
            var manifest = new JObject
            {
                ["format"] = Format,
                ["version"] = Version,
                ["status"] = diagnostics.Count == 0 ? "complete" : "partial",
                ["shader"] = new JObject
                {
                    ["name"] = shader.Name,
                    ["pathId"] = shader.m_PathID,
                    ["sourceFile"] = shader.assetsFile.fileName,
                    ["sourceOriginalPath"] = shader.assetsFile.originalPath,
                    ["game"] = shader.assetsFile.game.Type.ToString(),
                    ["objectSize"] = shader.byteSize,
                    ["useExternalBlobs"] = shader.m_UseExternalBlobs,
                    ["enableShaderLodStreaming"] = shader.m_EnableShaderLODStreaming,
                    ["compressionType"] = shader.m_CompressionType,
                    ["platforms"] = new JArray((shader.platforms ?? Array.Empty<ShaderCompilerPlatform>())
                        .Select(platform => platform.ToString())),
                },
                ["structure"] = structure,
                ["variantMappings"] = new JObject
                {
                    ["version"] = 2,
                    ["entries"] = variantMappingEntries,
                },
                ["blobs"] = blobs,
                ["diagnostics"] = diagnostics,
            };
            File.WriteAllText(
                Path.Combine(outputRoot, "manifest.json"),
                manifest.ToString(Formatting.Indented));
        }

        private static JToken ExportShaderStructure(Shader shader, JArray diagnostics)
        {
            var parsed = shader.m_ParsedForm;
            if (parsed == null)
            {
                AddDiagnostic(diagnostics, "shader/structure", "Shader has no parsed SerializedShader metadata.");
                return JValue.CreateNull();
            }

            var keywordNames = parsed.m_KeywordNames ?? Array.Empty<string>();
            var subShaders = new JArray();
            for (var subShaderIndex = 0; subShaderIndex < parsed.m_SubShaders.Count; subShaderIndex++)
            {
                var subShader = parsed.m_SubShaders[subShaderIndex];
                var passes = new JArray();
                for (var passIndex = 0; passIndex < subShader.m_Passes.Count; passIndex++)
                {
                    var pass = subShader.m_Passes[passIndex];
                    var passScope = $"shader/structure/subshader-{subShaderIndex}/pass-{passIndex}";
                    passes.Add(new JObject
                    {
                        ["index"] = passIndex,
                        ["name"] = pass.m_Name,
                        ["useName"] = pass.m_UseName,
                        ["type"] = pass.m_Type.ToString(),
                        ["programMask"] = $"0x{pass.m_ProgramMask:X8}",
                        ["tags"] = ExportTags(pass.m_Tags),
                        ["programs"] = new JObject
                        {
                            ["vertex"] = ExportProgram(pass.progVertex, keywordNames, pass.m_NameIndices, passScope + "/vertex", diagnostics),
                            ["fragment"] = ExportProgram(pass.progFragment, keywordNames, pass.m_NameIndices, passScope + "/fragment", diagnostics),
                            ["geometry"] = ExportProgram(pass.progGeometry, keywordNames, pass.m_NameIndices, passScope + "/geometry", diagnostics),
                            ["hull"] = ExportProgram(pass.progHull, keywordNames, pass.m_NameIndices, passScope + "/hull", diagnostics),
                            ["domain"] = ExportProgram(pass.progDomain, keywordNames, pass.m_NameIndices, passScope + "/domain", diagnostics),
                            ["rayTracing"] = ExportProgram(pass.progRayTracing, keywordNames, pass.m_NameIndices, passScope + "/ray-tracing", diagnostics),
                        },
                    });
                }

                subShaders.Add(new JObject
                {
                    ["index"] = subShaderIndex,
                    ["lod"] = subShader.m_LOD,
                    ["tags"] = ExportTags(subShader.m_Tags),
                    ["passes"] = passes,
                });
            }

            return new JObject
            {
                ["keywordNames"] = new JArray(keywordNames),
                ["differentMaterialCbKeywordNames"] = new JArray(
                    parsed.m_DifferentMaterialCbKeywordNames ?? Array.Empty<string>()),
                ["subShaders"] = subShaders,
            };
        }

        private static JArray ExportVariantMappings(
            JToken structure,
            JArray blobs,
            IReadOnlyList<ShaderCompilerPlatform> platforms,
            JArray diagnostics)
        {
            var mappings = new JArray();
            if (structure is not JObject structureObject || structureObject["subShaders"] is not JArray subShaders)
            {
                return mappings;
            }

            foreach (var subShader in subShaders.OfType<JObject>())
            {
                var subShaderIndex = subShader.Value<int>("index");
                var shaderLod = subShader.Value<int>("lod");
                if (subShader["passes"] is not JArray passes)
                {
                    continue;
                }

                foreach (var pass in passes.OfType<JObject>())
                {
                    var passIndex = pass.Value<int>("index");
                    var passName = pass.Value<string>("name") ?? string.Empty;
                    if (pass["programs"] is not JObject programs)
                    {
                        continue;
                    }

                    foreach (var programProperty in programs.Properties())
                    {
                        if (programProperty.Value is not JObject program)
                        {
                            continue;
                        }

                        if (program["flatSubPrograms"] is JArray flatSubPrograms)
                        {
                            for (var entryIndex = 0; entryIndex < flatSubPrograms.Count; entryIndex++)
                            {
                                if (flatSubPrograms[entryIndex] is not JObject entry)
                                {
                                    continue;
                                }
                                var scope =
                                    $"shader/variants/subshader-{subShaderIndex}/pass-{passIndex}/" +
                                    $"{programProperty.Name}/flat-{entryIndex}";
                                mappings.Add(ExportVariantMapping(
                                    scope,
                                    subShaderIndex,
                                    shaderLod,
                                    passIndex,
                                    passName,
                                    programProperty.Name,
                                    "flat",
                                    null,
                                    entryIndex,
                                    entry,
                                    blobs,
                                    platforms,
                                    diagnostics));
                            }
                        }

                        if (program["playerGroups"] is not JArray playerGroups)
                        {
                            continue;
                        }
                        foreach (var playerGroup in playerGroups.OfType<JObject>())
                        {
                            var groupIndex = playerGroup.Value<int>("index");
                            if (playerGroup["entries"] is not JArray entries)
                            {
                                continue;
                            }

                            for (var entryIndex = 0; entryIndex < entries.Count; entryIndex++)
                            {
                                if (entries[entryIndex] is not JObject entry)
                                {
                                    continue;
                                }
                                var scope =
                                    $"shader/variants/subshader-{subShaderIndex}/pass-{passIndex}/" +
                                    $"{programProperty.Name}/player-group-{groupIndex}/entry-{entryIndex}";
                                mappings.Add(ExportVariantMapping(
                                    scope,
                                    subShaderIndex,
                                    shaderLod,
                                    passIndex,
                                    passName,
                                    programProperty.Name,
                                    "player",
                                    groupIndex,
                                    entryIndex,
                                    entry,
                                    blobs,
                                    platforms,
                                    diagnostics));
                            }
                        }
                    }
                }
            }
            return mappings;
        }

        private static JObject ExportVariantMapping(
            string scope,
            int subShaderIndex,
            int shaderLod,
            int passIndex,
            string passName,
            string programSlot,
            string source,
            int? playerGroupIndex,
            int entryIndex,
            JObject entry,
            JArray blobs,
            IReadOnlyList<ShaderCompilerPlatform> platforms,
            JArray diagnostics)
        {
            var mapping = new JObject
            {
                ["id"] = scope,
                ["subShaderIndex"] = subShaderIndex,
                ["shaderLod"] = shaderLod,
                ["passIndex"] = passIndex,
                ["passName"] = passName,
                ["programSlot"] = programSlot,
                ["source"] = source,
                ["playerGroupIndex"] = playerGroupIndex.HasValue
                    ? JToken.FromObject(playerGroupIndex.Value)
                    : JValue.CreateNull(),
                ["entryIndex"] = entryIndex,
                ["programType"] = entry["programType"]?.DeepClone(),
                ["rawProgramType"] = entry["rawProgramType"]?.DeepClone(),
                ["platform"] = entry["platform"]?.DeepClone(),
                ["keywordIndices"] = entry["keywordIndices"]?.DeepClone() ?? new JArray(),
                ["keywords"] = entry["keywords"]?.DeepClone() ?? new JArray(),
                ["blobIndex"] = entry["blobIndex"]?.DeepClone(),
                ["parameterBlobIndex"] = entry["parameterBlobIndex"]?.DeepClone(),
            };

            var platformName = entry.Value<string>("platform");
            var platformIndex = FindPlatformIndex(platforms, platformName);
            if (platformIndex < 0)
            {
                mapping["status"] = "unresolved-platform";
                AddDiagnostic(diagnostics, scope, $"Platform '{platformName}' is not present in the Shader platform table.");
                return mapping;
            }
            mapping["platformIndex"] = platformIndex;

            var blobCandidates = blobs.OfType<JObject>()
                .Where(blob => blob["id"] != null && blob.Value<int?>("shaderLod") == shaderLod)
                .ToArray();
            if (blobCandidates.Length != 1)
            {
                mapping["status"] = "unresolved-lod-blob";
                AddDiagnostic(
                    diagnostics,
                    scope,
                    $"Expected one compiled blob for Shader LOD {shaderLod}, found {blobCandidates.Length}.");
                return mapping;
            }

            var blob = blobCandidates[0];
            var programRecord = ResolveSubProgramRecord(
                blob,
                platformIndex,
                entry.Value<uint>("blobIndex"),
                scope + "/program",
                diagnostics);
            var parameterIndex = entry.Value<uint?>("parameterBlobIndex");
            var parameterRecord = parameterIndex.HasValue
                ? ResolveSubProgramRecord(
                    blob,
                    platformIndex,
                    parameterIndex.Value,
                    scope + "/parameters",
                    diagnostics)
                : null;

            mapping["programRecord"] = programRecord == null
                ? JValue.CreateNull()
                : ExportRecordReference(blob, platformIndex, programRecord);
            mapping["parameterRecord"] = parameterRecord == null
                ? JValue.CreateNull()
                : ExportRecordReference(blob, platformIndex, parameterRecord);

            if (programRecord == null || (parameterIndex.HasValue && parameterRecord == null))
            {
                mapping["status"] = "unresolved-record";
                return mapping;
            }

            if (!string.Equals(programRecord.Value<string>("status"), "gpu-program-exported", StringComparison.Ordinal))
            {
                mapping["status"] = "program-payload-unavailable";
                AddDiagnostic(
                    diagnostics,
                    scope + "/program",
                    $"Resolved record status is '{programRecord.Value<string>("status")}', not 'gpu-program-exported'.");
                return mapping;
            }
            if (parameterRecord != null &&
                !string.Equals(parameterRecord.Value<string>("status"), "parameter-record-parsed", StringComparison.Ordinal))
            {
                mapping["status"] = "parameter-record-unavailable";
                AddDiagnostic(
                    diagnostics,
                    scope + "/parameters",
                    $"Resolved record status is '{parameterRecord.Value<string>("status")}', not 'parameter-record-parsed'.");
                return mapping;
            }

            var expectedProgramType = entry.Value<int>("rawProgramType");
            var actualProgramType = programRecord.Value<int?>("programType");
            if (actualProgramType != expectedProgramType)
            {
                mapping["status"] = "program-type-mismatch";
                AddDiagnostic(
                    diagnostics,
                    scope + "/program",
                    $"Structured program type {expectedProgramType} resolves to record type {actualProgramType}.");
                return mapping;
            }

            var structuredKeywords = entry["keywords"]?.Values<string>().ToArray() ?? Array.Empty<string>();
            var payloadKeywords = programRecord["keywords"]?.Values<string>().ToArray() ?? Array.Empty<string>();
            mapping["payloadKeywords"] = new JArray(payloadKeywords);
            var keywordsMatch = structuredKeywords.Length == payloadKeywords.Length &&
                structuredKeywords.OrderBy(keyword => keyword, StringComparer.Ordinal)
                    .SequenceEqual(
                        payloadKeywords.OrderBy(keyword => keyword, StringComparer.Ordinal),
                        StringComparer.Ordinal);
            mapping["keywordsMatch"] = keywordsMatch;
            if (!keywordsMatch)
            {
                AddDiagnostic(
                    diagnostics,
                    scope + "/keywords",
                    "SerializedProgram keywords do not match the resolved payload keywords.");
            }

            mapping["bindingValidation"] = ExportBindingValidation(programRecord, parameterRecord);
            mapping["status"] = "resolved";
            return mapping;
        }

        private static int FindPlatformIndex(
            IReadOnlyList<ShaderCompilerPlatform> platforms,
            string platformName)
        {
            for (var index = 0; index < platforms.Count; index++)
            {
                var candidate = platforms[index].ToString();
                if (string.Equals(candidate, platformName, StringComparison.OrdinalIgnoreCase) ||
                    (string.Equals(platformName, "d3d11", StringComparison.OrdinalIgnoreCase) &&
                     candidate.IndexOf("d3d11", StringComparison.OrdinalIgnoreCase) >= 0) ||
                    (string.Equals(platformName, "vulkan", StringComparison.OrdinalIgnoreCase) &&
                     candidate.IndexOf("vulkan", StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    return index;
                }
            }
            return -1;
        }

        private static JObject ResolveSubProgramRecord(
            JObject blob,
            int platformIndex,
            uint recordIndex,
            string scope,
            JArray diagnostics)
        {
            var matches = (blob["segments"] as JArray ?? new JArray())
                .OfType<JObject>()
                .Where(segment => segment.Value<int>("platformIndex") == platformIndex)
                .SelectMany(segment => (segment["subPrograms"] as JArray ?? new JArray())
                    .OfType<JObject>()
                    .Where(record => record.Value<uint>("index") == recordIndex &&
                                     record.Value<int>("sourceSegment") == segment.Value<int>("segmentIndex")))
                .ToArray();
            if (matches.Length != 1)
            {
                AddDiagnostic(
                    diagnostics,
                    scope,
                    $"Expected one stored record at platform {platformIndex}, index {recordIndex}, found {matches.Length}.");
                return null;
            }
            return matches[0];
        }

        private static JObject ExportRecordReference(JObject blob, int platformIndex, JObject record)
        {
            return new JObject
            {
                ["blobId"] = blob.Value<string>("id"),
                ["platformIndex"] = platformIndex,
                ["segmentIndex"] = record.Value<int>("sourceSegment"),
                ["entryIndex"] = record.Value<int>("index"),
                ["offset"] = record.Value<int>("offset"),
                ["length"] = record.Value<int>("length"),
                ["status"] = record.Value<string>("status"),
                ["rawFile"] = record["rawFile"]?.DeepClone() ?? JValue.CreateNull(),
            };
        }

        private static JObject ExportBindingValidation(JObject programRecord, JObject parameterRecord)
        {
            var reflectedBindingGroups = (programRecord["programSnippets"] as JArray ?? new JArray())
                .OfType<JObject>()
                .SelectMany(snippet => (snippet["descriptorBindings"] as JArray ?? new JArray())
                    .OfType<JObject>())
                .GroupBy(binding => (
                    Set: binding.Value<int>("descriptorSet"),
                    Binding: binding.Value<int>("binding")))
                .OrderBy(group => group.Key.Set)
                .ThenBy(group => group.Key.Binding)
                .ToArray();
            var reflectedBindings = reflectedBindingGroups.Select(group => group.Key).ToArray();
            var reflectionStatuses = (programRecord["programSnippets"] as JArray ?? new JArray())
                .OfType<JObject>()
                .Select(snippet => snippet.Value<string>("descriptorBindingsStatus"))
                .Where(status => !string.IsNullOrEmpty(status))
                .Distinct()
                .ToArray();
            if (parameterRecord?["parameterGroupMetadata"]?["descriptorSets"] is not JArray descriptorSets)
            {
                return new JObject
                {
                    ["status"] = "parameter-record-unavailable",
                    ["reflectionStatuses"] = new JArray(reflectionStatuses),
                };
            }

            var parameterBindingGroups = descriptorSets.OfType<JObject>()
                .SelectMany(set => (set["bindings"] as JArray ?? new JArray())
                    .OfType<JObject>()
                    .Select(binding => new
                    {
                        SetName = set.Value<string>("name"),
                        Set = set.Value<int>("setId"),
                        Binding = binding.Value<int>("bindingIndex"),
                        Value = binding,
                    }))
                .GroupBy(binding => (binding.Set, binding.Binding))
                .OrderBy(group => group.Key.Set)
                .ThenBy(group => group.Key.Binding)
                .ToArray();
            var parameterBindings = parameterBindingGroups.Select(group => group.Key).ToArray();
            var ambiguousParameterBindings = parameterBindingGroups
                .Where(group => group.Count() != 1)
                .ToArray();
            var missingInParameters = reflectedBindings.Except(parameterBindings).ToArray();
            var uniqueParameterBindings = parameterBindingGroups
                .Where(group => group.Count() == 1)
                .ToDictionary(group => group.Key, group => group.Single());
            var resolvedBindings = new JArray();
            foreach (var group in reflectedBindingGroups)
            {
                if (!uniqueParameterBindings.TryGetValue(group.Key, out var parameter))
                {
                    continue;
                }
                resolvedBindings.Add(new JObject
                {
                    ["descriptorSet"] = group.Key.Set,
                    ["descriptorSetName"] = parameter.SetName,
                    ["binding"] = group.Key.Binding,
                    ["name"] = parameter.Value.Value<string>("name"),
                    ["descriptorType"] = parameter.Value.Value<string>("descriptorType"),
                    ["rawDescriptorType"] = parameter.Value.Value<int>("rawDescriptorType"),
                    ["storageClasses"] = new JArray(group
                        .Select(binding => binding.Value<string>("storageClass"))
                        .Where(storageClass => !string.IsNullOrEmpty(storageClass))
                        .Distinct(StringComparer.Ordinal)
                        .OrderBy(storageClass => storageClass, StringComparer.Ordinal)),
                    ["rawStorageClasses"] = new JArray(group
                        .Select(binding => binding.Value<int>("rawStorageClass"))
                        .Distinct()
                        .OrderBy(storageClass => storageClass)),
                    ["packedBinding"] = parameter.Value["packedBinding"]?.DeepClone() ?? JValue.CreateNull(),
                    ["packedInfo"] = parameter.Value["packedInfo"]?.DeepClone() ?? JValue.CreateNull(),
                });
            }

            return new JObject
            {
                ["status"] = GetBindingValidationStatus(
                    reflectionStatuses,
                    missingInParameters.Length,
                    ambiguousParameterBindings.Length),
                ["reflectionStatuses"] = new JArray(reflectionStatuses),
                ["parameterBindingCount"] = parameterBindings.Length,
                ["reflectedProgramBindingCount"] = reflectedBindings.Length,
                ["resolvedBindings"] = resolvedBindings,
                ["missingInParameterRecord"] = new JArray(missingInParameters.Select(binding => new JObject
                {
                    ["descriptorSet"] = binding.Set,
                    ["binding"] = binding.Binding,
                })),
                ["ambiguousParameterBindings"] = new JArray(ambiguousParameterBindings.Select(group => new JObject
                {
                    ["descriptorSet"] = group.Key.Set,
                    ["binding"] = group.Key.Binding,
                    ["count"] = group.Count(),
                })),
            };
        }

        private static string GetBindingValidationStatus(
            IReadOnlyCollection<string> reflectionStatuses,
            int missingBindingCount,
            int ambiguousBindingCount)
        {
            if (ambiguousBindingCount > 0)
            {
                return "parameter-bindings-ambiguous";
            }
            if (!reflectionStatuses.Contains("reflected", StringComparer.Ordinal))
            {
                return "program-reflection-unavailable";
            }
            return missingBindingCount == 0
                ? "reflected-bindings-covered"
                : "reflected-bindings-missing";
        }

        private static JObject ExportProgram(
            SerializedProgram program,
            IReadOnlyList<string> keywordNames,
            IReadOnlyList<KeyValuePair<string, int>> nameIndices,
            string scope,
            JArray diagnostics)
        {
            if (program == null)
            {
                return new JObject
                {
                    ["flatSubPrograms"] = new JArray(),
                    ["playerGroups"] = new JArray(),
                    ["commonDescriptorSets"] = new JArray(),
                };
            }

            var flatSubPrograms = new JArray();
            var flat = program.m_SubPrograms ?? new List<SerializedSubProgram>();
            for (var index = 0; index < flat.Count; index++)
            {
                flatSubPrograms.Add(ExportSubProgram(
                    flat[index].m_BlobIndex,
                    null,
                    flat[index].m_GpuProgramType,
                    flat[index].m_KeywordIndices,
                    keywordNames,
                    $"{scope}/flat-{index}",
                    diagnostics));
            }

            var playerGroups = new JArray();
            var groups = program.m_PlayerSubPrograms ?? new List<List<SerializedPlayerSubProgram>>();
            var parameterGroups = program.m_ParameterBlobIndices ?? Array.Empty<uint[]>();
            if (groups.Count != parameterGroups.Length)
            {
                AddDiagnostic(
                    diagnostics,
                    scope,
                    $"PlayerSubPrograms has {groups.Count} groups, but ParameterBlobIndices has {parameterGroups.Length} groups.");
            }

            for (var groupIndex = 0; groupIndex < groups.Count; groupIndex++)
            {
                var entries = groups[groupIndex];
                var parameterIndices = groupIndex < parameterGroups.Length
                    ? parameterGroups[groupIndex] ?? Array.Empty<uint>()
                    : Array.Empty<uint>();
                if (entries.Count != parameterIndices.Length)
                {
                    AddDiagnostic(
                        diagnostics,
                        $"{scope}/player-group-{groupIndex}",
                        $"Player group has {entries.Count} entries, but its parameter group has {parameterIndices.Length} entries.");
                }

                var exportedEntries = new JArray();
                for (var entryIndex = 0; entryIndex < entries.Count; entryIndex++)
                {
                    var entry = entries[entryIndex];
                    uint? parameterBlobIndex = entryIndex < parameterIndices.Length
                        ? parameterIndices[entryIndex]
                        : null;
                    exportedEntries.Add(ExportSubProgram(
                        entry.m_BlobIndex,
                        parameterBlobIndex,
                        entry.m_GpuProgramType,
                        entry.m_KeywordIndices,
                        keywordNames,
                        $"{scope}/player-group-{groupIndex}/entry-{entryIndex}",
                        diagnostics));
                }

                playerGroups.Add(new JObject
                {
                    ["index"] = groupIndex,
                    ["entries"] = exportedEntries,
                });
            }

            return new JObject
            {
                ["flatSubPrograms"] = flatSubPrograms,
                ["playerGroups"] = playerGroups,
                ["commonDescriptorSets"] = ExportDescriptorSets(
                    program.m_CommonParameters?.m_DescriptorSetParams,
                    nameIndices),
            };
        }

        private static JArray ExportDescriptorSets(
            IReadOnlyList<DescriptorSetParam> descriptorSets,
            IReadOnlyList<KeyValuePair<string, int>> nameIndices)
        {
            var names = (nameIndices ?? Array.Empty<KeyValuePair<string, int>>())
                .GroupBy(pair => pair.Value)
                .ToDictionary(group => group.Key, group => group.First().Key);
            return new JArray((descriptorSets ?? Array.Empty<DescriptorSetParam>()).Select(set => new JObject
            {
                ["nameIndex"] = set.m_NameIndex,
                ["name"] = names.TryGetValue(set.m_NameIndex, out var setName)
                    ? JToken.FromObject(setName)
                    : JValue.CreateNull(),
                ["setId"] = set.m_SetId,
                ["maxBindingIndex"] = set.m_MaxBindingIndex,
                ["bindings"] = new JArray((set.m_SetBindings ?? new List<SetBinding>()).Select(binding => new JObject
                {
                    ["nameIndex"] = binding.m_NameIndex,
                    ["name"] = names.TryGetValue(binding.m_NameIndex, out var bindingName)
                        ? JToken.FromObject(bindingName)
                        : JValue.CreateNull(),
                    ["bindingIndex"] = binding.m_BindingIndex,
                    ["descriptorType"] = binding.m_DescriptorType,
                    ["packedBinding"] = $"0x{binding.m_PackedBinding:X8}",
                    ["packedInfo"] = $"0x{binding.m_PackedInfo:X8}",
                })),
            }));
        }

        private static JObject ExportSubProgram(
            uint blobIndex,
            uint? parameterBlobIndex,
            ShaderGpuProgramType programType,
            ushort[] keywordIndices,
            IReadOnlyList<string> keywordNames,
            string scope,
            JArray diagnostics)
        {
            var rawProgramType = (int)programType;
            var result = new JObject
            {
                ["blobIndex"] = blobIndex,
                ["parameterBlobIndex"] = parameterBlobIndex.HasValue
                    ? JToken.FromObject(parameterBlobIndex.Value)
                    : JValue.CreateNull(),
                ["programType"] = GetProgramTypeName(rawProgramType),
                ["rawProgramType"] = rawProgramType,
                ["platform"] = GetProgramPlatform(rawProgramType),
                ["keywordIndices"] = new JArray(keywordIndices ?? Array.Empty<ushort>()),
            };

            var resolvedKeywords = new JArray();
            foreach (var keywordIndex in keywordIndices ?? Array.Empty<ushort>())
            {
                if (keywordIndex >= keywordNames.Count)
                {
                    AddDiagnostic(
                        diagnostics,
                        scope,
                        $"Keyword index {keywordIndex} exceeds the keyword table size {keywordNames.Count}.");
                    continue;
                }
                resolvedKeywords.Add(keywordNames[keywordIndex]);
            }
            result["keywords"] = resolvedKeywords;
            return result;
        }

        private static JObject ExportTags(SerializedTagMap tagMap)
        {
            var result = new JObject();
            if (tagMap?.tags == null)
            {
                return result;
            }
            foreach (var tag in tagMap.tags)
            {
                result[tag.Key] = tag.Value;
            }
            return result;
        }

        private static string GetProgramTypeName(int programType)
        {
            if (programType == EndfieldD3D11ProgramType)
            {
                return "EndfieldD3D11";
            }
            return Enum.IsDefined(typeof(ShaderGpuProgramType), programType)
                ? ((ShaderGpuProgramType)programType).ToString()
                : "Unknown";
        }

        private static string GetProgramPlatform(int programType)
        {
            if (programType == (int)ShaderGpuProgramType.SPIRV)
            {
                return "vulkan";
            }
            if (programType == EndfieldD3D11ProgramType ||
                (programType >= (int)ShaderGpuProgramType.DX11VertexSM40 &&
                 programType <= (int)ShaderGpuProgramType.DX11DomainSM50))
            {
                return "d3d11";
            }
            return "unknown";
        }

        private static int? GetExternalLod(Shader shader, int index)
        {
            if (shader.m_SubShaderBinaryDataLODs == null || index >= shader.m_SubShaderBinaryDataLODs.Length)
            {
                return null;
            }
            return shader.m_SubShaderBinaryDataLODs[index];
        }

        private static void ExportBlob(
            string outputRoot,
            string id,
            string storage,
            int? shaderLod,
            byte[] compressedBlob,
            uint[][] offsets,
            uint[][] compressedLengths,
            uint[][] decompressedLengths,
            JArray blobs,
            JArray diagnostics,
            string elementEncoding = "bytes")
        {
            var blobRoot = Path.Combine(outputRoot, id);
            Directory.CreateDirectory(blobRoot);
            compressedBlob ??= Array.Empty<byte>();
            File.WriteAllBytes(Path.Combine(blobRoot, "compressed-blob.bin"), compressedBlob);

            var segments = new JArray();
            var blobInfo = new JObject
            {
                ["id"] = id,
                ["storage"] = storage,
                ["shaderLod"] = shaderLod.HasValue ? JToken.FromObject(shaderLod.Value) : JValue.CreateNull(),
                ["elementEncoding"] = elementEncoding,
                ["compressedBlobSize"] = compressedBlob.Length,
                ["segments"] = segments,
            };
            blobs.Add(blobInfo);

            if (!HaveMatchingPlatformDimensions(offsets, compressedLengths, decompressedLengths))
            {
                AddDiagnostic(diagnostics, id, "Offset and length tables have different platform dimensions.");
                return;
            }

            for (var platformIndex = 0; platformIndex < offsets.Length; platformIndex++)
            {
                if (offsets[platformIndex].Length != compressedLengths[platformIndex].Length ||
                    offsets[platformIndex].Length != decompressedLengths[platformIndex].Length)
                {
                    AddDiagnostic(
                        diagnostics,
                        $"{id}/platform-{platformIndex:D3}",
                        "Offset and length tables have different segment counts.");
                    continue;
                }

                for (var segmentIndex = 0; segmentIndex < offsets[platformIndex].Length; segmentIndex++)
                {
                    ExportSegment(
                        blobRoot,
                        id,
                        platformIndex,
                        segmentIndex,
                        compressedBlob,
                        offsets[platformIndex][segmentIndex],
                        compressedLengths[platformIndex][segmentIndex],
                        decompressedLengths[platformIndex][segmentIndex],
                        segments,
                        diagnostics);
                }
            }
        }

        private static bool HaveMatchingPlatformDimensions(params uint[][][] tables)
        {
            return tables.All(table => table != null) && tables.Select(table => table.Length).Distinct().Count() == 1;
        }

        private static bool HasSegments(uint[][] table)
        {
            return table != null && table.Any(row => row != null && row.Length > 0);
        }

        private static void ExportSegment(
            string blobRoot,
            string blobId,
            int platformIndex,
            int segmentIndex,
            byte[] source,
            uint offset,
            uint compressedLength,
            uint decompressedLength,
            JArray segments,
            JArray diagnostics)
        {
            var segmentId = $"platform-{platformIndex:D3}-segment-{segmentIndex:D3}";
            var info = new JObject
            {
                ["platformIndex"] = platformIndex,
                ["segmentIndex"] = segmentIndex,
                ["offset"] = offset,
                ["compressedLength"] = compressedLength,
                ["decompressedLength"] = decompressedLength,
            };
            segments.Add(info);

            if ((ulong)offset + compressedLength > (ulong)source.Length)
            {
                info["status"] = "invalid-range";
                AddDiagnostic(
                    diagnostics,
                    $"{blobId}/{segmentId}",
                    $"Range [{offset}, {(ulong)offset + compressedLength}) exceeds blob size {source.Length}.");
                return;
            }
            if (compressedLength > int.MaxValue || decompressedLength > int.MaxValue)
            {
                info["status"] = "unsupported-size";
                AddDiagnostic(diagnostics, $"{blobId}/{segmentId}", "Segment length exceeds the supported in-memory size.");
                return;
            }

            var compressed = source.AsSpan((int)offset, (int)compressedLength).ToArray();
            var compressedName = segmentId + ".lz4.bin";
            File.WriteAllBytes(Path.Combine(blobRoot, compressedName), compressed);
            info["compressedFile"] = compressedName;

            try
            {
                var decompressed = new byte[(int)decompressedLength];
                var written = LZ4.Instance.Decompress(compressed, decompressed);
                if (written != decompressed.Length)
                {
                    throw new InvalidDataException(
                        $"LZ4 wrote {written} bytes, expected {decompressed.Length} bytes.");
                }

                var decompressedName = segmentId + ".program-container.bin";
                File.WriteAllBytes(Path.Combine(blobRoot, decompressedName), decompressed);
                info["status"] = "decompressed";
                info["decompressedFile"] = decompressedName;
                info["payloadKind"] = IdentifyPayload(decompressed);
                info["subPrograms"] = ExportSubPrograms(
                    blobRoot,
                    blobId,
                    segmentId,
                    segmentIndex,
                    decompressed,
                    diagnostics);
            }
            catch (Exception ex) when (ex is InvalidDataException || ex is ArgumentException)
            {
                info["status"] = "decompression-failed";
                AddDiagnostic(diagnostics, $"{blobId}/{segmentId}", ex.Message);
            }
        }

        private static string IdentifyPayload(byte[] data)
        {
            if (data.Length >= 4 && data[0] == (byte)'D' && data[1] == (byte)'X' && data[2] == (byte)'B' && data[3] == (byte)'C')
            {
                return "dxbc-container";
            }
            if (data.Length >= 4 && BitConverter.ToUInt32(data, 0) == 0x07230203)
            {
                return "spir-v-module";
            }
            return "unity-shader-program-segment";
        }

        private static JArray ExportSubPrograms(
            string blobRoot,
            string blobId,
            string segmentId,
            int segmentIndex,
            byte[] data,
            JArray diagnostics)
        {
            var result = new JArray();
            if (data.Length < sizeof(int))
            {
                AddDiagnostic(diagnostics, $"{blobId}/{segmentId}", "Program segment is too short for its entry count.");
                return result;
            }

            var count = BinaryPrimitives.ReadInt32LittleEndian(data);
            var tableEnd = sizeof(int) + (long)count * 12;
            if (count < 0 || tableEnd > data.Length)
            {
                AddDiagnostic(
                    diagnostics,
                    $"{blobId}/{segmentId}",
                    $"Invalid ShaderProgram entry count {count} for {data.Length} bytes.");
                return result;
            }

            var programRoot = Path.Combine(blobRoot, segmentId + ".subprograms");
            Directory.CreateDirectory(programRoot);
            for (var index = 0; index < count; index++)
            {
                var entryOffset = sizeof(int) + index * 12;
                var offset = BinaryPrimitives.ReadInt32LittleEndian(data.AsSpan(entryOffset));
                var length = BinaryPrimitives.ReadInt32LittleEndian(data.AsSpan(entryOffset + 4));
                var sourceSegment = BinaryPrimitives.ReadInt32LittleEndian(data.AsSpan(entryOffset + 8));
                var info = new JObject
                {
                    ["index"] = index,
                    ["offset"] = offset,
                    ["length"] = length,
                    ["sourceSegment"] = sourceSegment,
                };
                result.Add(info);
                if (sourceSegment != segmentIndex)
                {
                    info["status"] = "stored-in-other-segment";
                    continue;
                }
                if (offset < tableEnd || length < 8 || (long)offset + length > data.Length)
                {
                    info["status"] = "invalid-range";
                    AddDiagnostic(
                        diagnostics,
                        $"{blobId}/{segmentId}/subprogram-{index:D4}",
                        $"Invalid subprogram range [{offset}, {(long)offset + length}) for {data.Length} bytes.");
                    continue;
                }

                var record = data.AsSpan(offset, length).ToArray();
                var recordName = $"subprogram-{index:D4}.bin";
                File.WriteAllBytes(Path.Combine(programRoot, recordName), record);
                info["status"] = "raw-exported";
                info["rawFile"] = Path.Combine(segmentId + ".subprograms", recordName).Replace('\\', '/');
                var version = BinaryPrimitives.ReadInt32LittleEndian(record);
                var programType = BinaryPrimitives.ReadInt32LittleEndian(record.AsSpan(4));
                info["version"] = version;
                info["versionHex"] = $"0x{version:X8}";
                info["programType"] = programType;

                if (version != EndfieldSubProgramVersion)
                {
                    continue;
                }
                if (programType != (int)ShaderGpuProgramType.SPIRV && programType != EndfieldD3D11ProgramType)
                {
                    if (EndfieldShaderParameterRecordReader.TryRead(
                        record,
                        out var parameterRecord,
                        out var parameterDiagnostic))
                    {
                        info["status"] = "parameter-record-parsed";
                        info["parameterGroupMetadata"] = ExportParameterRecord(parameterRecord);
                    }
                    else
                    {
                        info["parameterRecordDiagnostic"] = parameterDiagnostic;
                        AddDiagnostic(
                            diagnostics,
                            $"{blobId}/{segmentId}/subprogram-{index:D4}/parameters",
                            parameterDiagnostic);
                    }
                    continue;
                }

                try
                {
                    var code = ReadEndfieldProgramCode(record, out var keywords);
                    // 程序类型来自 Unity 元数据，但载荷仍可能带终末地自己的封装或压缩层。
                    // 在验证出标准文件头以前统一保留为 .bin，避免伪装成可直接使用的 SPIR-V/DXBC。
                    var codeName = $"subprogram-{index:D4}.gpu-program.bin";
                    File.WriteAllBytes(Path.Combine(programRoot, codeName), code);
                    info["status"] = "gpu-program-exported";
                    info["keywords"] = new JArray(keywords);
                    info["programFile"] = Path.Combine(segmentId + ".subprograms", codeName).Replace('\\', '/');
                    info["programSize"] = code.Length;
                    info["declaredProgramKind"] =
                        programType == (int)ShaderGpuProgramType.SPIRV ? "spir-v" : "endfield-d3d11";
                    info["payloadKind"] = IdentifyPayload(code);
                    ExportGpuProgramContainer(
                        programRoot,
                        blobId,
                        segmentId,
                        index,
                        programType,
                        code,
                        info,
                        diagnostics);
                }
                catch (InvalidDataException ex)
                {
                    info["status"] = "gpu-program-parse-failed";
                    AddDiagnostic(diagnostics, $"{blobId}/{segmentId}/subprogram-{index:D4}", ex.Message);
                }
            }
            return result;
        }

        private static JObject ExportParameterRecord(EndfieldShaderParameterRecord record)
        {
            return new JObject
            {
                ["version"] = record.Version,
                ["versionHex"] = $"0x{record.Version:X8}",
                ["groups"] = new JArray(record.Groups.Select(group => new JObject
                {
                    ["name"] = group.Name,
                    ["usedSize"] = group.UsedSize,
                    ["parameters"] = new JArray(group.Parameters.Select(ExportParameter)),
                    ["structs"] = new JArray(group.Structs.Select(structure => new JObject
                    {
                        ["name"] = structure.Name,
                        ["index"] = structure.Index,
                        ["arraySize"] = structure.ArraySize,
                        ["size"] = structure.Size,
                        ["parameters"] = new JArray(structure.Parameters.Select(ExportParameter)),
                    })),
                })),
                ["bindingDataOffset"] = record.BindingDataOffset,
                ["bindingDataLength"] = record.BindingDataLength,
                ["bindingDataStatus"] = "parsed",
                ["resourceBindings"] = new JArray(record.ResourceBindings.Select(binding => new JObject
                {
                    ["name"] = binding.Name,
                    ["type"] = GetResourceBindingTypeName(binding.Type),
                    ["rawType"] = binding.Type,
                    ["packedIndexOffset"] = binding.PackedIndexOffset,
                    ["packedIndex"] = $"0x{binding.PackedIndex:X8}",
                    ["extraValue"] = binding.ExtraValue,
                    ["textureExtra"] = binding.TextureExtra.HasValue
                        ? JToken.FromObject($"0x{binding.TextureExtra.Value:X8}")
                        : JValue.CreateNull(),
                })),
                ["descriptorSets"] = new JArray(record.DescriptorSets.Select(set => new JObject
                {
                    ["name"] = set.Name,
                    ["setId"] = set.SetId,
                    ["maxBindingIndex"] = set.MaxBindingIndex,
                    ["bindings"] = new JArray(set.Bindings.Select(binding => new JObject
                    {
                        ["name"] = binding.Name,
                        ["bindingIndex"] = binding.BindingIndex,
                        ["descriptorType"] = GetDescriptorTypeName(binding.DescriptorType),
                        ["rawDescriptorType"] = binding.DescriptorType,
                        ["packedBindingOffset"] = binding.PackedBindingOffset,
                        ["packedBinding"] = $"0x{binding.PackedBinding:X8}",
                        ["packedInfoOffset"] = binding.PackedInfoOffset,
                        ["packedInfo"] = $"0x{binding.PackedInfo:X8}",
                    })),
                })),
            };
        }

        private static string GetResourceBindingTypeName(int type)
        {
            return type switch
            {
                0 => "texture",
                1 => "constantBuffer",
                2 => "buffer",
                3 => "unorderedAccessView",
                4 => "sampler",
                _ => throw new ArgumentOutOfRangeException(nameof(type)),
            };
        }

        private static string GetDescriptorTypeName(int type)
        {
            return type switch
            {
                0 => "sampler",
                1 => "combinedImageSampler",
                2 => "sampledImage",
                3 => "storageImage",
                4 => "uniformTexelBuffer",
                5 => "storageTexelBuffer",
                6 => "uniformBuffer",
                7 => "storageBuffer",
                8 => "dynamicUniformBuffer",
                9 => "dynamicStorageBuffer",
                10 => "inputAttachment",
                _ => throw new ArgumentOutOfRangeException(nameof(type)),
            };
        }

        private static JObject ExportParameter(EndfieldShaderParameter parameter)
        {
            return new JObject
            {
                ["name"] = parameter.Name,
                ["type"] = GetParameterTypeName(parameter.Type),
                ["rawType"] = parameter.Type,
                ["rows"] = parameter.Rows,
                ["columns"] = parameter.Columns,
                ["isMatrix"] = parameter.IsMatrix,
                ["arraySize"] = parameter.ArraySize,
                ["index"] = parameter.Index,
            };
        }

        private static string GetParameterTypeName(int type)
        {
            return type switch
            {
                0 => "float",
                1 => "int",
                2 => "bool",
                3 => "half",
                4 => "short",
                5 => "uint",
                _ => throw new ArgumentOutOfRangeException(nameof(type)),
            };
        }

        private static void ExportGpuProgramContainer(
            string programRoot,
            string blobId,
            string segmentId,
            int subProgramIndex,
            int programType,
            byte[] code,
            JObject info,
            JArray diagnostics)
        {
            var payloadKind = programType == EndfieldD3D11ProgramType
                ? EndfieldGpuProgramPayloadKind.Dxbc
                : EndfieldGpuProgramPayloadKind.Smolv;
            if (!EndfieldGpuProgramContainer.TryParse(code, payloadKind, out var container, out var diagnostic))
            {
                info["programContainerStatus"] = "raw-only";
                AddDiagnostic(
                    diagnostics,
                    $"{blobId}/{segmentId}/subprogram-{subProgramIndex:D4}/gpu-program",
                    diagnostic);
                return;
            }

            var containerName = $"subprogram-{subProgramIndex:D4}.gpu-program";
            var containerRoot = Path.Combine(programRoot, containerName);
            Directory.CreateDirectory(containerRoot);
            var snippets = new JArray();
            foreach (var snippet in container.Snippets)
            {
                var snippetName = $"snippet-{snippet.TableIndex:D2}";
                var snippetInfo = new JObject
                {
                    ["tableIndex"] = snippet.TableIndex,
                    ["offset"] = snippet.Offset,
                    ["size"] = snippet.Size,
                };
                if (payloadKind == EndfieldGpuProgramPayloadKind.Dxbc)
                {
                    var fileName = snippetName + ".dxbc";
                    File.WriteAllBytes(Path.Combine(containerRoot, fileName), snippet.Data);
                    snippetInfo["file"] = Path.Combine(containerName, fileName).Replace('\\', '/');
                    snippetInfo["format"] = "dxbc";
                    snippetInfo["disassemblyStatus"] = "unsupported";
                    snippetInfo["disassemblyDiagnostic"] =
                        "In-process D3DCompiler disassembly is disabled because current Endfield DXBC can crash the native disassembler.";
                    if (ShaderProgramMetadataReader.TryReadDxbcStage(
                        snippet.Data,
                        out var stage,
                        out var rawStage,
                        out var stageDiagnostic))
                    {
                        snippetInfo["stage"] = GetStageName(stage);
                        snippetInfo["rawStage"] = rawStage;
                    }
                    else
                    {
                        snippetInfo["stageDiagnostic"] = stageDiagnostic;
                        AddDiagnostic(
                            diagnostics,
                            $"{blobId}/{segmentId}/subprogram-{subProgramIndex:D4}/gpu-program/snippet-{snippet.TableIndex:D2}/stage",
                            stageDiagnostic);
                    }
                    if (ShaderProgramMetadataReader.TryReadDxbcResourceBindings(
                        snippet.Data,
                        out var resourceBindings,
                        out var reflectionDiagnostic))
                    {
                        snippetInfo["resourceBindingsStatus"] = "reflected";
                        snippetInfo["resourceBindings"] = new JArray(resourceBindings.Select(binding => new JObject
                        {
                            ["name"] = binding.Name,
                            ["type"] = GetDxbcResourceTypeName(binding.Type),
                            ["rawType"] = binding.Type,
                            ["bindPoint"] = binding.BindPoint,
                            ["bindCount"] = binding.BindCount,
                            ["flags"] = $"0x{binding.Flags:X8}",
                            ["returnType"] = binding.ReturnType,
                            ["dimension"] = binding.Dimension,
                            ["sampleCount"] = binding.SampleCount,
                        }));
                    }
                    else
                    {
                        snippetInfo["resourceBindingsStatus"] = "unavailable";
                        snippetInfo["resourceBindingsDiagnostic"] = reflectionDiagnostic;
                    }
                }
                else
                {
                    var smolvName = snippetName + ".smolv";
                    File.WriteAllBytes(Path.Combine(containerRoot, smolvName), snippet.Data);
                    snippetInfo["file"] = Path.Combine(containerName, smolvName).Replace('\\', '/');
                    snippetInfo["format"] = "smol-v";
                    if (snippet.DecodedData != null)
                    {
                        var spirvName = snippetName + ".spv";
                        File.WriteAllBytes(Path.Combine(containerRoot, spirvName), snippet.DecodedData);
                        snippetInfo["decodedFile"] = Path.Combine(containerName, spirvName).Replace('\\', '/');
                        snippetInfo["decodedFormat"] = "spir-v";
                        snippetInfo["decodedSize"] = snippet.DecodedData.Length;
                        ExportDisassembly(
                            containerRoot,
                            containerName,
                            snippetName + ".spvasm",
                            ShaderProgramDisassembler.TryDisassembleSpirv,
                            snippet.DecodedData,
                            snippetInfo,
                            $"{blobId}/{segmentId}/subprogram-{subProgramIndex:D4}/gpu-program/snippet-{snippet.TableIndex:D2}",
                            diagnostics);
                        if (ShaderProgramMetadataReader.TryReadSpirvEntryPoints(
                            snippet.DecodedData,
                            out var entryPoints,
                            out var stageDiagnostic))
                        {
                            snippetInfo["entryPoints"] = new JArray(entryPoints.Select(entryPoint => new JObject
                            {
                                ["name"] = entryPoint.Name,
                                ["stage"] = GetStageName(entryPoint.Stage),
                                ["rawStage"] = entryPoint.RawStage,
                            }));
                        }
                        else
                        {
                            snippetInfo["stageDiagnostic"] = stageDiagnostic;
                            AddDiagnostic(
                                diagnostics,
                                $"{blobId}/{segmentId}/subprogram-{subProgramIndex:D4}/gpu-program/snippet-{snippet.TableIndex:D2}/stage",
                                stageDiagnostic);
                        }
                        if (ShaderProgramMetadataReader.TryReadSpirvDescriptorBindings(
                            snippet.DecodedData,
                            out var descriptorBindings,
                            out var reflectionDiagnostic))
                        {
                            snippetInfo["descriptorBindingsStatus"] = "reflected";
                            snippetInfo["descriptorBindings"] = new JArray(descriptorBindings.Select(binding => new JObject
                            {
                                ["id"] = binding.Id,
                                ["name"] = binding.Name == null ? JValue.CreateNull() : binding.Name,
                                ["descriptorSet"] = binding.DescriptorSet,
                                ["binding"] = binding.Binding,
                                ["storageClass"] = GetSpirvStorageClassName(binding.StorageClass),
                                ["rawStorageClass"] = binding.StorageClass,
                            }));
                        }
                        else
                        {
                            snippetInfo["descriptorBindingsStatus"] = "unavailable";
                            snippetInfo["descriptorBindingsDiagnostic"] = reflectionDiagnostic;
                        }
                    }
                    if (snippet.Diagnostic != null)
                    {
                        snippetInfo["diagnostic"] = snippet.Diagnostic;
                        AddDiagnostic(
                            diagnostics,
                            $"{blobId}/{segmentId}/subprogram-{subProgramIndex:D4}/gpu-program/snippet-{snippet.TableIndex:D2}",
                            snippet.Diagnostic);
                    }
                }
                snippets.Add(snippetInfo);
            }

            info["programContainerStatus"] = "parsed";
            info["programContainerEncoding"] = "endfield-gpu-program-table-v1";
            info["programContainerFlags"] = $"0x{container.Flags:X8}";
            info["programSnippets"] = snippets;
        }

        private delegate bool TryDisassembleProgram(
            byte[] data,
            out string text,
            out string diagnostic);

        private static void ExportDisassembly(
            string outputRoot,
            string relativeRoot,
            string fileName,
            TryDisassembleProgram disassemble,
            byte[] data,
            JObject snippetInfo,
            string scope,
            JArray diagnostics)
        {
            if (disassemble(data, out var text, out var diagnostic))
            {
                File.WriteAllText(Path.Combine(outputRoot, fileName), text);
                snippetInfo["disassemblyStatus"] = "exported";
                snippetInfo["disassemblyFile"] = Path.Combine(relativeRoot, fileName).Replace('\\', '/');
                return;
            }

            snippetInfo["disassemblyStatus"] = "failed";
            snippetInfo["disassemblyDiagnostic"] = diagnostic;
            AddDiagnostic(diagnostics, scope + "/disassembly", diagnostic);
        }

        private static string GetStageName(ShaderProgramStage stage)
        {
            var name = stage.ToString();
            return char.ToLowerInvariant(name[0]) + name.Substring(1);
        }

        private static string GetDxbcResourceTypeName(uint type)
        {
            return type switch
            {
                0 => "constantBuffer",
                1 => "textureBuffer",
                2 => "texture",
                3 => "sampler",
                4 => "readWriteTyped",
                5 => "structuredBuffer",
                6 => "readWriteStructuredBuffer",
                7 => "byteAddressBuffer",
                8 => "readWriteByteAddressBuffer",
                9 => "appendStructuredBuffer",
                10 => "consumeStructuredBuffer",
                11 => "readWriteStructuredBufferWithCounter",
                12 => "rayTracingAccelerationStructure",
                13 => "feedbackTexture",
                _ => "unknown",
            };
        }

        private static string GetSpirvStorageClassName(uint storageClass)
        {
            return storageClass switch
            {
                0 => "uniformConstant",
                2 => "uniform",
                9 => "pushConstant",
                12 => "storageBuffer",
                _ => "unknown",
            };
        }

        private static byte[] ReadEndfieldProgramCode(byte[] record, out IReadOnlyList<string> keywords)
        {
            var position = 24;
            var values = new List<string>();
            var keywordCount = ReadInt32(record, ref position, "keyword count");
            if (keywordCount < 0 || keywordCount > 1024)
            {
                throw new InvalidDataException($"Invalid Endfield keyword count {keywordCount}.");
            }
            for (var index = 0; index < keywordCount; index++)
            {
                var length = ReadInt32(record, ref position, $"keyword {index} length");
                if (length < 0 || position + (long)length > record.Length)
                {
                    throw new InvalidDataException($"Keyword {index} exceeds its subprogram record.");
                }
                values.Add(System.Text.Encoding.UTF8.GetString(record, position, length));
                position = (position + length + 3) & ~3;
                if (position > record.Length)
                {
                    throw new InvalidDataException($"Keyword {index} alignment exceeds its subprogram record.");
                }
            }

            var programLength = ReadInt32(record, ref position, "program length");
            if (programLength < 0 || position + (long)programLength > record.Length)
            {
                throw new InvalidDataException($"Program length {programLength} exceeds its subprogram record.");
            }
            keywords = values;
            return record.AsSpan(position, programLength).ToArray();
        }

        private static int ReadInt32(byte[] data, ref int position, string fieldName)
        {
            if (position < 0 || position + sizeof(int) > data.Length)
            {
                throw new InvalidDataException($"No room for {fieldName} at offset {position}.");
            }
            var value = BinaryPrimitives.ReadInt32LittleEndian(data.AsSpan(position));
            position += sizeof(int);
            return value;
        }

        private static void AddDiagnostic(JArray diagnostics, string scope, string message)
        {
            diagnostics.Add(new JObject
            {
                ["scope"] = scope,
                ["message"] = message,
            });
        }
    }
}
