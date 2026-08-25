namespace Vfs.Endfield.Extensions;

/// <summary>
/// 不携带领域对象身份的通用文件产物。领域导出需要额外字段时应定义更具体的产物类型。
/// </summary>
public sealed record ExportArtifact(
    string RelativePath,
    int ByteCount,
    string Sha256);
