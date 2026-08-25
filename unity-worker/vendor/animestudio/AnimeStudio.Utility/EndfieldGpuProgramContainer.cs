using Smolv;
using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace AnimeStudio
{
    public enum EndfieldGpuProgramPayloadKind
    {
        Dxbc,
        Smolv,
    }

    public sealed class EndfieldGpuProgramSnippet
    {
        public int TableIndex { get; init; }
        public int Offset { get; init; }
        public int Size { get; init; }
        public byte[] Data { get; init; }
        public byte[] DecodedData { get; init; }
        public string Diagnostic { get; init; }
    }

    public sealed class EndfieldGpuProgramContainer
    {
        private const int HeaderSize = 176;
        private const int TableOffset = sizeof(uint);
        private const int TableEntryCount = 6;
        private const uint SpirvMagic = 0x07230203;
        private EndfieldGpuProgramContainer(uint flags, IReadOnlyList<EndfieldGpuProgramSnippet> snippets)
        {
            Flags = flags;
            Snippets = snippets;
        }

        /// <summary>
        /// 首部前四字节的原始值。其位语义尚未确认，因此只保留、不解释。
        /// </summary>
        public uint Flags { get; }

        public IReadOnlyList<EndfieldGpuProgramSnippet> Snippets { get; }

        public static bool TryParse(
            byte[] data,
            EndfieldGpuProgramPayloadKind payloadKind,
            out EndfieldGpuProgramContainer container,
            out string diagnostic)
        {
            container = null;
            diagnostic = null;
            if (data == null || data.Length < HeaderSize)
            {
                diagnostic = $"GPU program container is shorter than the verified {HeaderSize}-byte header.";
                return false;
            }

            var entries = new List<(int TableIndex, int Offset, int Size)>();
            var foundEmptyEntry = false;
            for (var index = 0; index < TableEntryCount; index++)
            {
                var position = TableOffset + index * 2 * sizeof(uint);
                var offset = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position));
                var size = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(position + sizeof(uint)));
                if (offset == 0 && size == 0)
                {
                    foundEmptyEntry = true;
                    continue;
                }
                if (foundEmptyEntry)
                {
                    diagnostic = $"GPU program table entry {index} follows an empty entry.";
                    return false;
                }
                if (offset > int.MaxValue || size > int.MaxValue || size == 0)
                {
                    diagnostic = $"GPU program table entry {index} has an unsupported range ({offset}, {size}).";
                    return false;
                }
                entries.Add((index, (int)offset, (int)size));
            }

            if (entries.Count == 0)
            {
                diagnostic = "GPU program table contains no payload entries.";
                return false;
            }

            var physicalEntries = entries.OrderBy(entry => entry.Offset).ToArray();
            if (physicalEntries[0].Offset != HeaderSize)
            {
                diagnostic = $"First GPU program payload begins at {physicalEntries[0].Offset}, expected {HeaderSize}.";
                return false;
            }

            for (var index = 0; index < physicalEntries.Length; index++)
            {
                var entry = physicalEntries[index];
                var expectedEnd = index + 1 < physicalEntries.Length
                    ? physicalEntries[index + 1].Offset
                    : data.Length;
                if ((long)entry.Offset + entry.Size != expectedEnd)
                {
                    diagnostic = $"GPU program payload {entry.TableIndex} does not end at the next verified boundary.";
                    return false;
                }
            }

            var snippets = new List<EndfieldGpuProgramSnippet>(entries.Count);
            foreach (var entry in entries)
            {
                var snippet = data.AsSpan(entry.Offset, entry.Size).ToArray();
                byte[] decoded = null;
                string snippetDiagnostic = null;
                if (payloadKind == EndfieldGpuProgramPayloadKind.Dxbc)
                {
                    if (!TryValidateDxbc(snippet, out diagnostic))
                    {
                        diagnostic = $"GPU program table entry {entry.TableIndex}: {diagnostic}";
                        return false;
                    }
                }
                else
                {
                    if (!TryValidateSmolv(snippet, out diagnostic))
                    {
                        diagnostic = $"GPU program table entry {entry.TableIndex}: {diagnostic}";
                        return false;
                    }
                    TryDecodeSmolv(snippet, out decoded, out snippetDiagnostic);
                }

                snippets.Add(new EndfieldGpuProgramSnippet
                {
                    TableIndex = entry.TableIndex,
                    Offset = entry.Offset,
                    Size = entry.Size,
                    Data = snippet,
                    DecodedData = decoded,
                    Diagnostic = snippetDiagnostic,
                });
            }

            container = new EndfieldGpuProgramContainer(
                BinaryPrimitives.ReadUInt32LittleEndian(data),
                snippets);
            return true;
        }

        private static bool TryValidateSmolv(byte[] data, out string diagnostic)
        {
            diagnostic = null;
            if (data.Length < 24 ||
                data[0] != (byte)'L' || data[1] != (byte)'O' || data[2] != (byte)'M' || data[3] != (byte)'S')
            {
                diagnostic = "payload does not begin with the little-endian SMOL-V marker.";
                return false;
            }

            var versionWord = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4));
            var spirvVersion = versionWord & 0x00FFFFFF;
            if (spirvVersion < 0x00010000 || spirvVersion > 0x00010600)
            {
                diagnostic = $"SMOL-V declares unsupported SPIR-V version 0x{spirvVersion:X6}.";
                return false;
            }

            var decodedSize = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(20));
            if (decodedSize < 20 || decodedSize > int.MaxValue || decodedSize % sizeof(uint) != 0)
            {
                diagnostic = $"SMOL-V declares invalid decoded size {decodedSize}.";
                return false;
            }
            return true;
        }

        private static bool TryDecodeSmolv(byte[] data, out byte[] decoded, out string diagnostic)
        {
            decoded = null;
            diagnostic = null;
            try
            {
                decoded = SmolvDecoder.Decode(data);
            }
            catch (Exception ex) when (
                ex is ArgumentException || ex is InvalidDataException ||
                ex is EndOfStreamException || ex is IndexOutOfRangeException)
            {
                diagnostic = $"The bundled SMOL-V decoder rejected this encoding ({ex.GetType().Name}); raw bytes were preserved.";
                return false;
            }

            if (decoded == null)
            {
                diagnostic = "The SMOL-V payload uses an encoding not supported by the bundled decoder; raw bytes were preserved.";
                return false;
            }
            if (decoded.Length < 20 || decoded.Length % sizeof(uint) != 0 ||
                BinaryPrimitives.ReadUInt32LittleEndian(decoded) != SpirvMagic)
            {
                decoded = null;
                diagnostic = "The SMOL-V decoder did not produce a valid SPIR-V module; raw bytes were preserved.";
                return false;
            }
            return true;
        }

        private static bool TryValidateDxbc(byte[] data, out string diagnostic)
        {
            diagnostic = null;
            if (data.Length < 32 ||
                data[0] != (byte)'D' || data[1] != (byte)'X' || data[2] != (byte)'B' || data[3] != (byte)'C')
            {
                diagnostic = "payload does not begin with DXBC.";
                return false;
            }
            if (BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(24)) != data.Length)
            {
                diagnostic = "DXBC internal length does not match its table entry.";
                return false;
            }

            var chunkCount = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(28));
            if (chunkCount > (data.Length - 32) / sizeof(uint))
            {
                diagnostic = $"DXBC chunk count {chunkCount} exceeds its container.";
                return false;
            }
            for (var index = 0; index < chunkCount; index++)
            {
                var chunkOffset = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(32 + index * sizeof(uint)));
                if (chunkOffset > data.Length - 8)
                {
                    diagnostic = $"DXBC chunk {index} begins outside its container.";
                    return false;
                }
                var chunkSize = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan((int)chunkOffset + sizeof(uint)));
                if ((ulong)chunkOffset + 8 + chunkSize > (ulong)data.Length)
                {
                    diagnostic = $"DXBC chunk {index} exceeds its container.";
                    return false;
                }
            }
            return true;
        }
    }
}
