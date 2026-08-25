using System.Text.Json;

namespace Vfs.UnityWorker;

internal static class Program
{
    public static int Main(string[] args)
    {
        var operation = args.Length > 0 ? args[0] : string.Empty;
        WorkerResponse response;
        if (string.Equals(operation, "request", StringComparison.Ordinal))
        {
            try
            {
                if (args.Length != 2)
                {
                    throw new ArgumentException("request 操作需要且只需要一个 JSON 文件路径。");
                }
                response = WorkerProtocol.HandleRequestJson(File.ReadAllText(args[1]));
            }
            catch (Exception exception) when (
                exception is ArgumentException or IOException or UnauthorizedAccessException)
            {
                response = WorkerResponse.Failure(new WorkerError(
                    "invalid_request_file",
                    exception.Message,
                    false));
            }
        }
        else
        {
            response = WorkerProtocol.Handle(operation);
        }
        Console.WriteLine(JsonSerializer.Serialize(response, WorkerProtocol.JsonOptions));
        return response.Ok ? 0 : 2;
    }
}
