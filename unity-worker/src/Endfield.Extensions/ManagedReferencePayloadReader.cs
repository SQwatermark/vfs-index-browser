using System.Buffers.Binary;
using System.Text;

namespace Vfs.Endfield.Extensions;

/// <summary>
/// managed-reference 单项 payload 的有界读取器。所有读取都必须携带字段路径，使格式错误能
/// 精确指向消费位置；读取器本身不包含任何具体游戏字段语义。
/// </summary>
public sealed class ManagedReferencePayloadReader
{
    private static readonly Encoding StrictUtf8 = new UTF8Encoding(false, true);
    private readonly byte[] rawData;
    private readonly int start;
    private readonly int end;

    public ManagedReferencePayloadReader(byte[] rawData, int offset, int length)
    {
        this.rawData = rawData ?? throw new InvalidDataException("payload 字节为空。");
        if (offset < 0 || length < 0 || offset > rawData.Length - length)
        {
            throw new InvalidDataException("payload 范围超出 Raw 数据边界。");
        }
        start = offset;
        Position = offset;
        end = offset + length;
    }

    public byte[] RawData => rawData;

    public int Position { get; private set; }

    public int End => end;

    public int Remaining => end - Position;

    public void SetPosition(int position)
    {
        if (position < start || position > end)
        {
            throw new InvalidDataException("payload 读取位置超出当前条目边界。");
        }
        Position = position;
    }

    public void EnsureComplete()
    {
        if (Position != end)
        {
            throw new InvalidDataException(
                $"payload 未完整消费：停在 {Position}，条目结束于 {end}。");
        }
    }

    public int ReadInt32(string fieldPath)
    {
        EnsureAvailable(4, fieldPath);
        var value = BinaryPrimitives.ReadInt32LittleEndian(rawData.AsSpan(Position, 4));
        Position += 4;
        return value;
    }

    public long ReadInt64(string fieldPath)
    {
        EnsureAvailable(8, fieldPath);
        var value = BinaryPrimitives.ReadInt64LittleEndian(rawData.AsSpan(Position, 8));
        Position += 8;
        return value;
    }

    public float ReadFloat(string fieldPath)
    {
        var value = BitConverter.Int32BitsToSingle(ReadInt32(fieldPath));
        if (!float.IsFinite(value))
        {
            throw new InvalidDataException($"{fieldPath} 包含非有限 float。");
        }
        return value;
    }

    public double ReadDouble(string fieldPath)
    {
        EnsureAvailable(8, fieldPath);
        var value = BitConverter.Int64BitsToDouble(
            BinaryPrimitives.ReadInt64LittleEndian(rawData.AsSpan(Position, 8)));
        Position += 8;
        if (!double.IsFinite(value))
        {
            throw new InvalidDataException($"{fieldPath} 包含非有限 double。");
        }
        return value;
    }

    public bool ReadBool32(string fieldPath)
    {
        var value = ReadInt32(fieldPath);
        if (value is not 0 and not 1)
        {
            throw new InvalidDataException($"{fieldPath} 包含非法 bool32：{value}。");
        }
        return value != 0;
    }

    public string ReadAlignedAsciiString(string fieldPath) =>
        ReadAlignedString(fieldPath, 512, asciiOnly: true);

    public string ReadAlignedUtf8String(string fieldPath) =>
        ReadAlignedString(fieldPath, 1024, asciiOnly: false);

    private string ReadAlignedString(string fieldPath, int maximumLength, bool asciiOnly)
    {
        var stringOffset = Position;
        var length = ReadInt32(fieldPath);
        if (length < 0 || length > maximumLength)
        {
            throw new InvalidDataException($"{fieldPath} 包含非法字符串长度：{length}。");
        }
        EnsureAvailable(length, fieldPath);

        string value;
        if (asciiOnly)
        {
            for (var index = Position; index < Position + length; index++)
            {
                if (rawData[index] is < 0x20 or > 0x7e)
                {
                    throw new InvalidDataException(
                        $"{fieldPath} 在偏移 {index} 包含非 ASCII 字节。");
                }
            }
            value = Encoding.ASCII.GetString(rawData, Position, length);
        }
        else
        {
            try
            {
                value = StrictUtf8.GetString(rawData, Position, length);
            }
            catch (DecoderFallbackException exception)
            {
                throw new InvalidDataException($"{fieldPath} 包含非法 UTF-8。", exception);
            }
        }

        Position = Align4(Position + length);
        if (Position > end)
        {
            throw new InvalidDataException(
                $"{fieldPath} 从 {stringOffset} 开始，对齐后越过 payload 结尾。");
        }
        return value;
    }

    private void EnsureAvailable(int byteCount, string fieldPath)
    {
        if (byteCount < 0 || Position > end - byteCount)
        {
            throw new InvalidDataException($"{fieldPath} 没有足够的剩余字节。");
        }
    }

    private static int Align4(int value) => (value + 3) & ~3;
}
