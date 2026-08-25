using SpirV;
using System;
using System.IO;

namespace AnimeStudio
{
    public static class ShaderProgramDisassembler
    {
        public static bool TryDisassembleSpirv(byte[] data, out string text, out string diagnostic)
        {
            text = null;
            diagnostic = null;
            if (data == null || data.Length == 0)
            {
                diagnostic = "SPIR-V payload is empty.";
                return false;
            }

            try
            {
                using var stream = new MemoryStream(data, writable: false);
                var module = Module.ReadFrom(stream);
                text = new Disassembler()
                    .Disassemble(module, DisassemblyOptions.Default)
                    .Replace("\r\n", "\n");
                if (string.IsNullOrWhiteSpace(text))
                {
                    diagnostic = "SPIR-V disassembler returned empty output.";
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                diagnostic = ex.Message;
                return false;
            }
        }
    }
}
