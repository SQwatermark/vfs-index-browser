using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Text;

namespace AnimeStudio
{
    public sealed class EndfieldShaderParameterRecord
    {
        public int Version { get; init; }
        public IReadOnlyList<EndfieldShaderParameterGroup> Groups { get; init; }
        public IReadOnlyList<EndfieldShaderResourceBinding> ResourceBindings { get; init; }
        public IReadOnlyList<EndfieldShaderDescriptorSet> DescriptorSets { get; init; }
        public int BindingDataOffset { get; init; }
        public int BindingDataLength { get; init; }
    }

    public sealed class EndfieldShaderParameterGroup
    {
        public string Name { get; init; }
        public int UsedSize { get; init; }
        public IReadOnlyList<EndfieldShaderParameter> Parameters { get; init; }
        public IReadOnlyList<EndfieldShaderStructParameter> Structs { get; init; }
    }

    public sealed class EndfieldShaderParameter
    {
        public string Name { get; init; }
        public int Type { get; init; }
        public int Rows { get; init; }
        public int Columns { get; init; }
        public bool IsMatrix { get; init; }
        public int ArraySize { get; init; }
        public int Index { get; init; }
    }

    public sealed class EndfieldShaderStructParameter
    {
        public string Name { get; init; }
        public int Index { get; init; }
        public int ArraySize { get; init; }
        public int Size { get; init; }
        public IReadOnlyList<EndfieldShaderParameter> Parameters { get; init; }
    }

    public sealed class EndfieldShaderResourceBinding
    {
        public string Name { get; init; }
        public int Type { get; init; }
        public int PackedIndexOffset { get; init; }
        public uint PackedIndex { get; init; }
        public int ExtraValue { get; init; }
        public uint? TextureExtra { get; init; }
    }

    public sealed class EndfieldShaderDescriptorSet
    {
        public string Name { get; init; }
        public int SetId { get; init; }
        public int MaxBindingIndex { get; init; }
        public IReadOnlyList<EndfieldShaderDescriptorBinding> Bindings { get; init; }
    }

    public sealed class EndfieldShaderDescriptorBinding
    {
        public string Name { get; init; }
        public int BindingIndex { get; init; }
        public int DescriptorType { get; init; }
        public int PackedBindingOffset { get; init; }
        public uint PackedBinding { get; init; }
        public int PackedInfoOffset { get; init; }
        public uint PackedInfo { get; init; }
    }

    /// <summary>
    /// Strictly reads Endfield parameter entries referenced by ParameterBlobIndices.
    /// Packed binding values are preserved without interpreting their bit fields.
    /// </summary>
    public static class EndfieldShaderParameterRecordReader
    {
        private const int EndfieldSubProgramVersion = 0x0C11FFE2;
        private const int MaxCollectionCount = 65536;
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

        public static bool TryRead(
            byte[] data,
            out EndfieldShaderParameterRecord record,
            out string diagnostic)
        {
            record = null;
            diagnostic = null;
            if (data == null)
            {
                diagnostic = "Parameter record is null.";
                return false;
            }

            try
            {
                var reader = new RecordReader(data);
                var version = reader.ReadInt32("version");
                if (version != EndfieldSubProgramVersion)
                {
                    throw new FormatException($"Unexpected parameter record version 0x{version:X8}.");
                }

                var groups = new List<EndfieldShaderParameterGroup>();
                var groupCount = reader.ReadCount("parameter group count");
                for (var groupIndex = 0; groupIndex < groupCount; groupIndex++)
                {
                    groups.Add(ReadGroup(ref reader, groupIndex));
                }

                var bindingDataOffset = reader.Position;
                var resourceBindings = ReadResourceBindings(ref reader);
                var descriptorSets = ReadDescriptorSets(ref reader);
                if (reader.Remaining != 0)
                {
                    throw new FormatException($"Parameter record has {reader.Remaining} trailing bytes.");
                }

                record = new EndfieldShaderParameterRecord
                {
                    Version = version,
                    Groups = groups,
                    ResourceBindings = resourceBindings,
                    DescriptorSets = descriptorSets,
                    BindingDataOffset = bindingDataOffset,
                    BindingDataLength = reader.Position - bindingDataOffset,
                };
                return true;
            }
            catch (Exception ex) when (ex is FormatException || ex is DecoderFallbackException)
            {
                diagnostic = ex.Message;
                return false;
            }
        }

        private static IReadOnlyList<EndfieldShaderResourceBinding> ReadResourceBindings(ref RecordReader reader)
        {
            var result = new List<EndfieldShaderResourceBinding>();
            var count = reader.ReadCount("resource binding count");
            for (var index = 0; index < count; index++)
            {
                var scope = $"resource binding {index}";
                var name = reader.ReadAlignedString(scope + " name");
                var type = reader.ReadInt32(scope + " type");
                if (type < 0 || type > 4)
                {
                    throw new FormatException($"{scope} has unknown type {type}.");
                }
                var packedIndexOffset = reader.Position;
                result.Add(new EndfieldShaderResourceBinding
                {
                    Name = name,
                    Type = type,
                    PackedIndexOffset = packedIndexOffset,
                    PackedIndex = reader.ReadUInt32(scope + " packed index"),
                    ExtraValue = reader.ReadInt32(scope + " extra value"),
                    TextureExtra = type == 0
                        ? reader.ReadUInt32(scope + " texture extra")
                        : null,
                });
            }
            return result;
        }

        private static IReadOnlyList<EndfieldShaderDescriptorSet> ReadDescriptorSets(ref RecordReader reader)
        {
            var result = new List<EndfieldShaderDescriptorSet>();
            var count = reader.ReadCount("descriptor set count");
            for (var setIndex = 0; setIndex < count; setIndex++)
            {
                var scope = $"descriptor set {setIndex}";
                var name = reader.ReadAlignedString(scope + " name");
                var setId = reader.ReadNonNegativeInt32(scope + " id");
                var bindingCount = reader.ReadCount(scope + " binding count");
                var maxBindingIndex = reader.ReadInt32(scope + " max binding index");
                if ((bindingCount == 0 && maxBindingIndex != -1) ||
                    (bindingCount > 0 && maxBindingIndex < bindingCount - 1))
                {
                    throw new FormatException(
                        $"{scope} has binding count {bindingCount} and invalid max index {maxBindingIndex}.");
                }

                var bindings = new List<EndfieldShaderDescriptorBinding>();
                for (var bindingIndex = 0; bindingIndex < bindingCount; bindingIndex++)
                {
                    var bindingScope = $"{scope} binding {bindingIndex}";
                    var bindingName = reader.ReadAlignedString(bindingScope + " name");
                    var actualBindingIndex = reader.ReadNonNegativeInt32(bindingScope + " index");
                    if (actualBindingIndex > maxBindingIndex)
                    {
                        throw new FormatException(
                            $"{bindingScope} index {actualBindingIndex} exceeds max index {maxBindingIndex}.");
                    }
                    var descriptorType = reader.ReadInt32(bindingScope + " descriptor type");
                    if (descriptorType < 0 || descriptorType > 10)
                    {
                        throw new FormatException(
                            $"{bindingScope} has unknown descriptor type {descriptorType}.");
                    }
                    var packedBindingOffset = reader.Position;
                    var packedBinding = reader.ReadUInt32(bindingScope + " packed binding");
                    var packedInfoOffset = reader.Position;
                    bindings.Add(new EndfieldShaderDescriptorBinding
                    {
                        Name = bindingName,
                        BindingIndex = actualBindingIndex,
                        DescriptorType = descriptorType,
                        PackedBindingOffset = packedBindingOffset,
                        PackedBinding = packedBinding,
                        PackedInfoOffset = packedInfoOffset,
                        PackedInfo = reader.ReadUInt32(bindingScope + " packed info"),
                    });
                }
                result.Add(new EndfieldShaderDescriptorSet
                {
                    Name = name,
                    SetId = setId,
                    MaxBindingIndex = maxBindingIndex,
                    Bindings = bindings,
                });
            }
            return result;
        }

        private static EndfieldShaderParameterGroup ReadGroup(ref RecordReader reader, int groupIndex)
        {
            var scope = $"parameter group {groupIndex}";
            var name = reader.ReadAlignedString(scope + " name");
            var usedSize = reader.ReadNonNegativeInt32(scope + " used size");
            var parameters = new List<EndfieldShaderParameter>();
            var parameterCount = reader.ReadCount(scope + " parameter count");
            for (var parameterIndex = 0; parameterIndex < parameterCount; parameterIndex++)
            {
                parameters.Add(ReadParameter(ref reader, $"{scope} parameter {parameterIndex}"));
            }

            var structs = new List<EndfieldShaderStructParameter>();
            var structCount = reader.ReadCount(scope + " struct count");
            for (var structIndex = 0; structIndex < structCount; structIndex++)
            {
                var structScope = $"{scope} struct {structIndex}";
                var structName = reader.ReadAlignedString(structScope + " name");
                var index = reader.ReadNonNegativeInt32(structScope + " index");
                var arraySize = reader.ReadNonNegativeInt32(structScope + " array size");
                var size = reader.ReadNonNegativeInt32(structScope + " size");
                var members = new List<EndfieldShaderParameter>();
                var memberCount = reader.ReadCount(structScope + " member count");
                for (var memberIndex = 0; memberIndex < memberCount; memberIndex++)
                {
                    members.Add(ReadParameter(ref reader, $"{structScope} member {memberIndex}"));
                }
                structs.Add(new EndfieldShaderStructParameter
                {
                    Name = structName,
                    Index = index,
                    ArraySize = arraySize,
                    Size = size,
                    Parameters = members,
                });
            }

            return new EndfieldShaderParameterGroup
            {
                Name = name,
                UsedSize = usedSize,
                Parameters = parameters,
                Structs = structs,
            };
        }

        private static EndfieldShaderParameter ReadParameter(ref RecordReader reader, string scope)
        {
            var name = reader.ReadAlignedString(scope + " name");
            var type = reader.ReadInt32(scope + " type");
            if (type < 0 || type > 5)
            {
                throw new FormatException($"{scope} has unknown parameter type {type}.");
            }

            var rows = reader.ReadNonNegativeInt32(scope + " rows");
            var columns = reader.ReadNonNegativeInt32(scope + " columns");
            if (rows > 4 || columns > 4)
            {
                throw new FormatException($"{scope} has invalid dimensions {rows}x{columns}.");
            }

            var matrixFlag = reader.ReadInt32(scope + " matrix flag");
            if (matrixFlag != 0 && matrixFlag != 1)
            {
                throw new FormatException($"{scope} has invalid matrix flag {matrixFlag}.");
            }

            return new EndfieldShaderParameter
            {
                Name = name,
                Type = type,
                Rows = rows,
                Columns = columns,
                IsMatrix = matrixFlag == 1,
                ArraySize = reader.ReadNonNegativeInt32(scope + " array size"),
                Index = reader.ReadNonNegativeInt32(scope + " index"),
            };
        }

        private ref struct RecordReader
        {
            private readonly ReadOnlySpan<byte> data;

            public RecordReader(byte[] data)
            {
                this.data = data;
                Position = 0;
            }

            public int Position { get; private set; }
            public int Remaining => data.Length - Position;

            public int ReadInt32(string fieldName)
            {
                EnsureAvailable(sizeof(int), fieldName);
                var value = BinaryPrimitives.ReadInt32LittleEndian(data.Slice(Position));
                Position += sizeof(int);
                return value;
            }

            public uint ReadUInt32(string fieldName)
            {
                EnsureAvailable(sizeof(uint), fieldName);
                var value = BinaryPrimitives.ReadUInt32LittleEndian(data.Slice(Position));
                Position += sizeof(uint);
                return value;
            }

            public int ReadNonNegativeInt32(string fieldName)
            {
                var value = ReadInt32(fieldName);
                if (value < 0)
                {
                    throw new FormatException($"{fieldName} cannot be negative ({value}).");
                }
                return value;
            }

            public int ReadCount(string fieldName)
            {
                var value = ReadNonNegativeInt32(fieldName);
                if (value > MaxCollectionCount)
                {
                    throw new FormatException($"{fieldName} exceeds the limit ({value}).");
                }
                return value;
            }

            public string ReadAlignedString(string fieldName)
            {
                var length = ReadCount(fieldName + " length");
                EnsureAvailable(length, fieldName);
                var value = StrictUtf8.GetString(data.Slice(Position, length));
                Position += length;
                Position = (Position + 3) & ~3;
                if (Position > data.Length)
                {
                    throw new FormatException($"{fieldName} alignment exceeds the record boundary.");
                }
                return value;
            }

            private void EnsureAvailable(int length, string fieldName)
            {
                if (length < 0 || Position > data.Length - length)
                {
                    throw new FormatException($"No room for {fieldName} at offset {Position}.");
                }
            }
        }
    }
}
