using System.Buffers.Binary;
using System.Text;

namespace Vfs.Endfield.Extensions;

public sealed record ManagedReferenceRegistry(
    int Offset,
    int Version,
    IReadOnlyList<ManagedReferenceEntry> Entries);

public sealed record ManagedReferenceEntry(
    long Rid,
    string ClassName,
    string Namespace,
    string AssemblyName,
    int HeaderOffset,
    int DataOffset,
    int DataLength,
    bool IsNullSentinel);

/// <summary>
/// 从 MonoBehaviour Raw 字节中识别 Unity managed-reference registry。该扫描器只恢复
/// registry 和每项 payload 的字节边界，不解释任何终末地字段。
/// </summary>
public static class ManagedReferenceRegistryScanner
{
    private const int MinimumHeaderBytes = 20;
    private const int MaximumEntryCount = 10_000;

    public static IReadOnlyList<ManagedReferenceRegistry> FindCandidates(byte[] rawData)
    {
        ArgumentNullException.ThrowIfNull(rawData);
        var candidates = new List<ManagedReferenceRegistry>();
        for (var offset = 0; offset <= rawData.Length - 8; offset += 4)
        {
            var version = BinaryPrimitives.ReadInt32LittleEndian(rawData.AsSpan(offset, 4));
            var count = BinaryPrimitives.ReadInt32LittleEndian(rawData.AsSpan(offset + 4, 4));
            if (version is < 1 or > 3 || count is <= 0 or > MaximumEntryCount)
            {
                continue;
            }

            if (TryReadRegistry(rawData, offset, version, count, out var registry))
            {
                candidates.Add(registry);
            }
        }
        return candidates;
    }

    private static bool TryReadRegistry(
        byte[] rawData,
        int registryOffset,
        int version,
        int count,
        out ManagedReferenceRegistry registry)
    {
        registry = null!;
        var headers = new List<Header>(count);
        var usedRids = new HashSet<long>();
        if (!TryReadHeader(rawData, registryOffset + 8, out var first) ||
            !usedRids.Add(first.Rid))
        {
            return false;
        }
        headers.Add(first);

        while (headers.Count < count)
        {
            var remaining = count - headers.Count;
            if (!TryFindNextHeader(
                rawData,
                headers[^1].DataOffset,
                remaining,
                usedRids,
                out var next) ||
                !usedRids.Add(next.Rid))
            {
                return false;
            }
            headers.Add(next);
        }

        var entries = new List<ManagedReferenceEntry>(count);
        for (var index = 0; index < headers.Count; index++)
        {
            var header = headers[index];
            var dataEnd = index + 1 < headers.Count
                ? headers[index + 1].HeaderOffset
                : rawData.Length;
            if (dataEnd < header.DataOffset)
            {
                return false;
            }
            entries.Add(new ManagedReferenceEntry(
                header.Rid,
                header.ClassName,
                header.Namespace,
                header.AssemblyName,
                header.HeaderOffset,
                header.DataOffset,
                dataEnd - header.DataOffset,
                header.IsNullSentinel));
        }

        registry = new ManagedReferenceRegistry(registryOffset, version, entries);
        return true;
    }

    private static bool TryFindNextHeader(
        byte[] rawData,
        int searchStart,
        int remainingCount,
        IReadOnlySet<long> usedRids,
        out Header header)
    {
        // 先要求强类型头，只有整条强链不存在时才接受 null sentinel。payload 中常见连续零值，
        // 若混在同一轮扫描会把普通数据误认成 null 项并提前截断后续真实类型。
        foreach (var requireStrongHeader in new[] { true, false })
        {
            var start = Align4(searchStart);
            var last = rawData.Length - remainingCount * MinimumHeaderBytes;
            for (var offset = start; offset <= last; offset += 4)
            {
                if (!TryReadHeader(rawData, offset, out var candidate) ||
                    usedRids.Contains(candidate.Rid) ||
                    (requireStrongHeader &&
                        (candidate.IsNullSentinel || !LooksLikeRuntimeType(candidate))) ||
                    (!requireStrongHeader &&
                        !candidate.IsNullSentinel && !LooksLikeRuntimeType(candidate)))
                {
                    continue;
                }

                var nextUsed = new HashSet<long>(usedRids) { candidate.Rid };
                if (remainingCount == 1 ||
                    CanReadRemainingHeaders(
                        rawData,
                        candidate.DataOffset,
                        remainingCount - 1,
                        nextUsed))
                {
                    header = candidate;
                    return true;
                }
            }
        }

        header = null!;
        return false;
    }

    private static bool CanReadRemainingHeaders(
        byte[] rawData,
        int searchStart,
        int remainingCount,
        IReadOnlySet<long> usedRids)
    {
        if (remainingCount == 0)
        {
            return true;
        }
        return TryFindNextHeader(rawData, searchStart, remainingCount, usedRids, out _);
    }

    private static bool TryReadHeader(byte[] rawData, int offset, out Header header)
    {
        header = null!;
        if (offset < 0 || offset > rawData.Length - 12)
        {
            return false;
        }

        var position = offset;
        var rid = BinaryPrimitives.ReadInt64LittleEndian(rawData.AsSpan(position, 8));
        position += 8;
        if (!TryReadAlignedAsciiString(rawData, ref position, out var className) ||
            !TryReadAlignedAsciiString(rawData, ref position, out var namespaceName) ||
            !TryReadAlignedAsciiString(rawData, ref position, out var assemblyName))
        {
            return false;
        }

        var isNullSentinel = rid < 0 &&
            className.Length == 0 &&
            namespaceName.Length == 0 &&
            assemblyName.Length == 0;
        if (rid == 0 ||
            (rid < 0 && !isNullSentinel) ||
            (!isNullSentinel &&
                (!LooksLikeTypePart(className) ||
                 !LooksLikeNamespace(namespaceName) ||
                 !LooksLikeAssemblyName(assemblyName))))
        {
            return false;
        }

        header = new Header(
            rid,
            className,
            namespaceName,
            assemblyName,
            offset,
            position,
            isNullSentinel);
        return true;
    }

    private static bool TryReadAlignedAsciiString(
        byte[] rawData,
        ref int position,
        out string value)
    {
        value = string.Empty;
        if (position > rawData.Length - 4)
        {
            return false;
        }

        var length = BinaryPrimitives.ReadInt32LittleEndian(rawData.AsSpan(position, 4));
        position += 4;
        if (length is < 0 or > 512 || position + length > rawData.Length)
        {
            return false;
        }
        for (var index = position; index < position + length; index++)
        {
            if (rawData[index] is < 0x20 or > 0x7e)
            {
                return false;
            }
        }

        value = Encoding.ASCII.GetString(rawData, position, length);
        position = Align4(position + length);
        return position <= rawData.Length;
    }

    private static bool LooksLikeRuntimeType(Header header) =>
        header.AssemblyName.Contains('.', StringComparison.Ordinal) &&
        header.Namespace.Contains('.', StringComparison.Ordinal);

    private static bool LooksLikeNamespace(string value) =>
        value.Length == 0 || value.Split('.').All(LooksLikeTypePart);

    private static bool LooksLikeTypePart(string value)
    {
        if (value.Length == 0 || !(char.IsLetter(value[0]) || value[0] == '_'))
        {
            return false;
        }
        return value.All(character =>
            char.IsLetterOrDigit(character) ||
            character is '_' or '`' or '<' or '>' or '+' or '/' or '[' or ']' or ',');
    }

    private static bool LooksLikeAssemblyName(string value)
    {
        if (value.Length == 0 || !(char.IsLetter(value[0]) || value[0] == '_'))
        {
            return false;
        }
        return value.All(character =>
            char.IsLetterOrDigit(character) || character is '_' or '.' or '-');
    }

    private static int Align4(int value) => (value + 3) & ~3;

    private sealed record Header(
        long Rid,
        string ClassName,
        string Namespace,
        string AssemblyName,
        int HeaderOffset,
        int DataOffset,
        bool IsNullSentinel);
}
