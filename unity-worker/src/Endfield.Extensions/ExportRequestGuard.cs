namespace Vfs.Endfield.Extensions;

/// <summary>
/// 所有 worker 文件导出共用的输入与独占输出目录边界。
/// </summary>
internal static class ExportRequestGuard
{
    public static (string InputPath, string OutputDirectory) Prepare(
        string inputPathValue,
        string outputDirectoryValue)
    {
        var inputPath = PrepareInput(inputPathValue);
        var outputDirectory = PrepareOutput(outputDirectoryValue);
        return (inputPath, outputDirectory);
    }

    public static string PrepareInput(string inputPathValue)
    {
        if (string.IsNullOrWhiteSpace(inputPathValue))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "inputPath 不能为空。");
        }

        var inputPath = Path.GetFullPath(inputPathValue);
        if (!File.Exists(inputPath))
        {
            throw new MonoBehaviourExportException(
                "input_not_found",
                $"输入 Bundle 不存在：{inputPath}");
        }
        return inputPath;
    }

    public static string PrepareOutput(string outputDirectoryValue)
    {
        if (string.IsNullOrWhiteSpace(outputDirectoryValue))
        {
            throw new MonoBehaviourExportException(
                "invalid_input",
                "outputDirectory 不能为空。");
        }

        var outputDirectory = Path.GetFullPath(outputDirectoryValue);
        if (Directory.Exists(outputDirectory))
        {
            if (Directory.EnumerateFileSystemEntries(outputDirectory).Any())
            {
                throw new MonoBehaviourExportException(
                    "output_not_empty",
                    $"输出目录必须是本次请求独占的空目录：{outputDirectory}");
            }
        }
        else
        {
            Directory.CreateDirectory(outputDirectory);
        }
        return outputDirectory;
    }
}
