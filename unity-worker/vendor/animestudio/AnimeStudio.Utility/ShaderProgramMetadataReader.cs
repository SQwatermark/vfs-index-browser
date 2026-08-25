using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace AnimeStudio
{
    public enum ShaderProgramStage
    {
        Unknown,
        Vertex,
        TessellationControl,
        TessellationEvaluation,
        Geometry,
        Fragment,
        Compute,
        RayGeneration,
        Intersection,
        AnyHit,
        ClosestHit,
        Miss,
        Callable,
        Task,
        Mesh,
    }

    public sealed class ShaderProgramEntryPoint
    {
        public string Name { get; init; }
        public ShaderProgramStage Stage { get; init; }
        public uint RawStage { get; init; }
    }

    /// <summary>
    /// 从标准 GPU 程序中读取入口阶段。这里只解析格式中有明确规范的字段，
    /// 不根据文件名、表项顺序或反编译结果猜测 Shader 阶段。
    /// </summary>
    public static class ShaderProgramMetadataReader
    {
        private const uint SpirvMagic = 0x07230203;
        private const ushort SpirvOpName = 5;
        private const ushort SpirvOpEntryPoint = 15;
        private const ushort SpirvOpVariable = 59;
        private const ushort SpirvOpDecorate = 71;
        private const uint SpirvDecorationBinding = 33;
        private const uint SpirvDecorationDescriptorSet = 34;
        private const uint DxbcMagic = 0x43425844;
        private const uint DxbcShaderChunk = 0x52444853; // SHDR
        private const uint DxbcExtendedShaderChunk = 0x58454853; // SHEX
        private const uint DxbcResourceDefinitionChunk = 0x46454452; // RDEF
        private const int DxbcChunkTableOffset = 32;
        private const int MaxDxbcChunkCount = 256;
        private const int DxbcResourceBindingSize = 32;
        private const int MaxDxbcResourceBindingCount = 65536;
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

        public sealed class DxbcResourceBinding
        {
            public string Name { get; init; }
            public uint Type { get; init; }
            public uint BindPoint { get; init; }
            public uint BindCount { get; init; }
            public uint Flags { get; init; }
            public uint ReturnType { get; init; }
            public uint Dimension { get; init; }
            public uint SampleCount { get; init; }
        }

        public sealed class SpirvDescriptorBinding
        {
            public uint Id { get; init; }
            public string Name { get; init; }
            public uint DescriptorSet { get; init; }
            public uint Binding { get; init; }
            public uint StorageClass { get; init; }
        }

        public static bool TryReadSpirvEntryPoints(
            byte[] data,
            out IReadOnlyList<ShaderProgramEntryPoint> entryPoints,
            out string diagnostic)
        {
            entryPoints = Array.Empty<ShaderProgramEntryPoint>();
            diagnostic = null;
            if (data == null || data.Length < 20 || data.Length % sizeof(uint) != 0 ||
                BinaryPrimitives.ReadUInt32LittleEndian(data) != SpirvMagic)
            {
                diagnostic = "SPIR-V module has an invalid header or byte length.";
                return false;
            }

            var result = new List<ShaderProgramEntryPoint>();
            var position = 20;
            while (position < data.Length)
            {
                var instruction = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position));
                var wordCount = (int)(instruction >> 16);
                var opCode = (ushort)instruction;
                if (wordCount == 0 || position + (long)wordCount * sizeof(uint) > data.Length)
                {
                    diagnostic = $"SPIR-V instruction at byte {position} exceeds the module.";
                    return false;
                }

                if (opCode == SpirvOpEntryPoint)
                {
                    if (wordCount < 4)
                    {
                        diagnostic = $"SPIR-V OpEntryPoint at byte {position} is truncated.";
                        return false;
                    }
                    var executionModel = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 4));
                    var stage = GetSpirvStage(executionModel);
                    if (stage == ShaderProgramStage.Unknown)
                    {
                        diagnostic = $"SPIR-V OpEntryPoint at byte {position} uses unknown execution model {executionModel}.";
                        return false;
                    }
                    if (!TryReadSpirvString(data, position + 12, position + wordCount * sizeof(uint), out var name))
                    {
                        diagnostic = $"SPIR-V OpEntryPoint at byte {position} has no terminated name.";
                        return false;
                    }
                    result.Add(new ShaderProgramEntryPoint
                    {
                        Name = name,
                        Stage = stage,
                        RawStage = executionModel,
                    });
                }
                position += wordCount * sizeof(uint);
            }

            if (result.Count == 0)
            {
                diagnostic = "SPIR-V module contains no OpEntryPoint instruction.";
                return false;
            }
            entryPoints = result;
            return true;
        }

        public static bool TryReadSpirvDescriptorBindings(
            byte[] data,
            out IReadOnlyList<SpirvDescriptorBinding> bindings,
            out string diagnostic)
        {
            bindings = Array.Empty<SpirvDescriptorBinding>();
            diagnostic = null;
            if (data == null || data.Length < 20 || data.Length % sizeof(uint) != 0 ||
                BinaryPrimitives.ReadUInt32LittleEndian(data) != SpirvMagic)
            {
                diagnostic = "SPIR-V module has an invalid header or byte length.";
                return false;
            }

            var idBound = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(12));
            var names = new Dictionary<uint, string>();
            var descriptorSets = new Dictionary<uint, uint>();
            var bindingPoints = new Dictionary<uint, uint>();
            var storageClasses = new Dictionary<uint, uint>();
            var position = 20;
            while (position < data.Length)
            {
                var instruction = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position));
                var wordCount = (int)(instruction >> 16);
                var opCode = (ushort)instruction;
                if (wordCount == 0 || position + (long)wordCount * sizeof(uint) > data.Length)
                {
                    diagnostic = $"SPIR-V instruction at byte {position} exceeds the module.";
                    return false;
                }

                if (opCode == SpirvOpName && wordCount >= 3)
                {
                    var id = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 4));
                    if (!TryReadSpirvString(data, position + 8, position + wordCount * sizeof(uint), out var name))
                    {
                        diagnostic = $"SPIR-V OpName at byte {position} has no terminated name.";
                        return false;
                    }
                    names[id] = name;
                }
                else if (opCode == SpirvOpDecorate && wordCount >= 4)
                {
                    var id = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 4));
                    var decoration = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 8));
                    var value = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 12));
                    if (decoration == SpirvDecorationBinding)
                    {
                        bindingPoints[id] = value;
                    }
                    else if (decoration == SpirvDecorationDescriptorSet)
                    {
                        descriptorSets[id] = value;
                    }
                }
                else if (opCode == SpirvOpVariable && wordCount >= 4)
                {
                    var id = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 8));
                    storageClasses[id] = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + 12));
                }
                position += wordCount * sizeof(uint);
            }

            var decoratedIds = descriptorSets.Keys.Union(bindingPoints.Keys).OrderBy(id => id).ToArray();
            var result = new List<SpirvDescriptorBinding>(decoratedIds.Length);
            foreach (var id in decoratedIds)
            {
                if (id == 0 || id >= idBound)
                {
                    diagnostic = $"SPIR-V descriptor id {id} exceeds the declared id bound {idBound}.";
                    return false;
                }
                if (!descriptorSets.TryGetValue(id, out var descriptorSet) ||
                    !bindingPoints.TryGetValue(id, out var bindingPoint))
                {
                    diagnostic = $"SPIR-V descriptor id {id} does not have both DescriptorSet and Binding decorations.";
                    return false;
                }
                if (!storageClasses.TryGetValue(id, out var storageClass))
                {
                    diagnostic = $"SPIR-V descriptor id {id} has no OpVariable declaration.";
                    return false;
                }
                result.Add(new SpirvDescriptorBinding
                {
                    Id = id,
                    Name = names.TryGetValue(id, out var name) ? name : null,
                    DescriptorSet = descriptorSet,
                    Binding = bindingPoint,
                    StorageClass = storageClass,
                });
            }
            bindings = result;
            return true;
        }

        public static bool TryReadDxbcStage(
            byte[] data,
            out ShaderProgramStage stage,
            out uint rawStage,
            out string diagnostic)
        {
            stage = ShaderProgramStage.Unknown;
            rawStage = 0;
            diagnostic = null;
            if (data == null || data.Length < DxbcChunkTableOffset ||
                BinaryPrimitives.ReadUInt32LittleEndian(data) != DxbcMagic)
            {
                diagnostic = "DXBC container has an invalid header.";
                return false;
            }

            var chunkCount = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(28));
            if (chunkCount == 0 || chunkCount > MaxDxbcChunkCount ||
                DxbcChunkTableOffset + (long)chunkCount * sizeof(uint) > data.Length)
            {
                diagnostic = $"DXBC chunk count {chunkCount} is invalid.";
                return false;
            }

            for (var index = 0; index < chunkCount; index++)
            {
                var chunkOffset = BinaryPrimitives.ReadUInt32LittleEndian(
                    data.AsSpan(DxbcChunkTableOffset + index * sizeof(uint)));
                if (chunkOffset > data.Length - 8)
                {
                    diagnostic = $"DXBC chunk {index} cannot contain a chunk header.";
                    return false;
                }

                var chunkType = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)chunkOffset));
                if (chunkType != DxbcShaderChunk && chunkType != DxbcExtendedShaderChunk)
                {
                    continue;
                }
                if (chunkOffset > data.Length - 12)
                {
                    diagnostic = $"DXBC shader chunk {index} cannot contain a version token.";
                    return false;
                }

                var chunkSize = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)chunkOffset + 4));
                if (chunkSize < sizeof(uint) || (ulong)chunkOffset + 8 + chunkSize > (ulong)data.Length)
                {
                    diagnostic = $"DXBC shader chunk {index} exceeds its container.";
                    return false;
                }

                var versionToken = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)chunkOffset + 8));
                rawStage = versionToken >> 16;
                stage = GetDxbcStage(rawStage);
                if (stage == ShaderProgramStage.Unknown)
                {
                    diagnostic = $"DXBC shader chunk {index} uses unknown program type {rawStage}.";
                    return false;
                }
                return true;
            }

            diagnostic = "DXBC container contains no SHDR or SHEX program chunk.";
            return false;
        }

        public static bool TryReadDxbcResourceBindings(
            byte[] data,
            out IReadOnlyList<DxbcResourceBinding> bindings,
            out string diagnostic)
        {
            bindings = Array.Empty<DxbcResourceBinding>();
            diagnostic = null;
            if (!TryFindDxbcChunk(data, DxbcResourceDefinitionChunk, out var chunk, out diagnostic))
            {
                return false;
            }
            if (chunk.Length < 28)
            {
                diagnostic = "DXBC RDEF chunk is too short for its header.";
                return false;
            }

            var resourceCount = BinaryPrimitives.ReadUInt32LittleEndian(chunk.Slice(8));
            var resourceOffset = BinaryPrimitives.ReadUInt32LittleEndian(chunk.Slice(12));
            if (resourceCount > MaxDxbcResourceBindingCount)
            {
                diagnostic = $"DXBC RDEF resource count {resourceCount} exceeds the limit.";
                return false;
            }
            if ((ulong)resourceOffset + (ulong)resourceCount * DxbcResourceBindingSize > (ulong)chunk.Length)
            {
                diagnostic = "DXBC RDEF resource binding table exceeds its chunk.";
                return false;
            }

            try
            {
                var result = new List<DxbcResourceBinding>((int)resourceCount);
                for (var index = 0; index < resourceCount; index++)
                {
                    var entryOffset = checked((int)resourceOffset + (int)index * DxbcResourceBindingSize);
                    var entry = chunk.Slice(entryOffset, DxbcResourceBindingSize);
                    var nameOffset = BinaryPrimitives.ReadUInt32LittleEndian(entry);
                    if (!TryReadNullTerminatedUtf8(chunk, nameOffset, out var name))
                    {
                        diagnostic = $"DXBC RDEF resource {index} has an invalid name offset {nameOffset}.";
                        return false;
                    }

                    result.Add(new DxbcResourceBinding
                    {
                        Name = name,
                        Type = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(4)),
                        BindPoint = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(8)),
                        BindCount = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(12)),
                        Flags = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(16)),
                        ReturnType = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(20)),
                        Dimension = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(24)),
                        SampleCount = BinaryPrimitives.ReadUInt32LittleEndian(entry.Slice(28)),
                    });
                }
                bindings = result;
                return true;
            }
            catch (DecoderFallbackException ex)
            {
                diagnostic = $"DXBC RDEF contains invalid UTF-8: {ex.Message}";
                return false;
            }
        }

        private static bool TryFindDxbcChunk(
            byte[] data,
            uint expectedType,
            out ReadOnlySpan<byte> chunk,
            out string diagnostic)
        {
            chunk = default;
            diagnostic = null;
            if (data == null || data.Length < DxbcChunkTableOffset ||
                BinaryPrimitives.ReadUInt32LittleEndian(data) != DxbcMagic)
            {
                diagnostic = "DXBC container has an invalid header.";
                return false;
            }

            var chunkCount = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(28));
            if (chunkCount == 0 || chunkCount > MaxDxbcChunkCount ||
                DxbcChunkTableOffset + (long)chunkCount * sizeof(uint) > data.Length)
            {
                diagnostic = $"DXBC chunk count {chunkCount} is invalid.";
                return false;
            }

            for (var index = 0; index < chunkCount; index++)
            {
                var chunkOffset = BinaryPrimitives.ReadUInt32LittleEndian(
                    data.AsSpan(DxbcChunkTableOffset + index * sizeof(uint)));
                if (chunkOffset > data.Length - 8)
                {
                    diagnostic = $"DXBC chunk {index} cannot contain a chunk header.";
                    return false;
                }
                var chunkSize = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)chunkOffset + 4));
                if ((ulong)chunkOffset + 8 + chunkSize > (ulong)data.Length)
                {
                    diagnostic = $"DXBC chunk {index} exceeds its container.";
                    return false;
                }
                if (BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)chunkOffset)) == expectedType)
                {
                    chunk = data.AsSpan((int)chunkOffset + 8, (int)chunkSize);
                    return true;
                }
            }

            diagnostic = $"DXBC container contains no {Encoding.ASCII.GetString(BitConverter.GetBytes(expectedType))} chunk.";
            return false;
        }

        private static bool TryReadNullTerminatedUtf8(ReadOnlySpan<byte> data, uint offset, out string value)
        {
            value = null;
            if (offset >= data.Length)
            {
                return false;
            }
            var tail = data.Slice((int)offset);
            var length = tail.IndexOf((byte)0);
            if (length < 0)
            {
                return false;
            }
            value = StrictUtf8.GetString(tail.Slice(0, length));
            return true;
        }

        private static bool TryReadSpirvString(byte[] data, int start, int end, out string value)
        {
            value = null;
            var terminator = Array.IndexOf(data, (byte)0, start, end - start);
            if (terminator < start)
            {
                return false;
            }
            var length = terminator - start;
            value = Encoding.UTF8.GetString(data, start, length);
            return true;
        }

        private static ShaderProgramStage GetDxbcStage(uint stage) => stage switch
        {
            0 => ShaderProgramStage.Fragment,
            1 => ShaderProgramStage.Vertex,
            2 => ShaderProgramStage.Geometry,
            3 => ShaderProgramStage.TessellationControl,
            4 => ShaderProgramStage.TessellationEvaluation,
            5 => ShaderProgramStage.Compute,
            _ => ShaderProgramStage.Unknown,
        };

        private static ShaderProgramStage GetSpirvStage(uint stage) => stage switch
        {
            0 => ShaderProgramStage.Vertex,
            1 => ShaderProgramStage.TessellationControl,
            2 => ShaderProgramStage.TessellationEvaluation,
            3 => ShaderProgramStage.Geometry,
            4 => ShaderProgramStage.Fragment,
            5 => ShaderProgramStage.Compute,
            5267 or 5313 => ShaderProgramStage.RayGeneration,
            5268 or 5314 => ShaderProgramStage.Intersection,
            5269 or 5315 => ShaderProgramStage.AnyHit,
            5270 or 5316 => ShaderProgramStage.ClosestHit,
            5271 or 5317 => ShaderProgramStage.Miss,
            5272 or 5318 => ShaderProgramStage.Callable,
            5364 => ShaderProgramStage.Task,
            5365 => ShaderProgramStage.Mesh,
            _ => ShaderProgramStage.Unknown,
        };
    }
}
