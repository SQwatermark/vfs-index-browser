using System;
using System.IO;
using System.Runtime.InteropServices;
using AnimeStudio.PInvoke;

namespace ACLLibs
{
    public readonly struct EndfieldACLTracks
    {
        public readonly float[] Values;
        public readonly float[] Times;
        public readonly int TrackCount;
        public readonly int ComponentsPerTrack;

        public EndfieldACLTracks(
            float[] values,
            float[] times,
            int trackCount,
            int componentsPerTrack)
        {
            Values = values;
            Times = times;
            TrackCount = trackCount;
            ComponentsPerTrack = componentsPerTrack;
        }
    }

    public static class EndfieldACL
    {
        private const string DLL_NAME = "acl_endfield";

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeTracks
        {
            public IntPtr Values;
            public uint ValuesCount;
            public IntPtr Times;
            public uint TimesCount;
            public uint TrackCount;
            public uint ComponentsPerTrack;
        }

        static EndfieldACL()
        {
            DllLoader.PreloadDll(DLL_NAME);
        }

        public static EndfieldACLTracks DecompressTransforms(byte[] data)
        {
            return Decompress(data, DecompressQvvf);
        }

        public static EndfieldACLTracks DecompressFloats(byte[] data)
        {
            return Decompress(data, DecompressFloat1f);
        }

        private delegate int DecompressFunction(
            byte[] data,
            nuint dataSize,
            ref NativeTracks tracks);

        private static EndfieldACLTracks Decompress(
            byte[] data,
            DecompressFunction decompress)
        {
            var native = new NativeTracks();
            var result = decompress(data, (nuint)data.Length, ref native);
            if (result != 0)
                throw new InvalidDataException($"Endfield ACL decompression failed with code {result}.");

            try
            {
                var values = new float[checked((int)native.ValuesCount)];
                var times = new float[checked((int)native.TimesCount)];
                Marshal.Copy(native.Values, values, 0, values.Length);
                Marshal.Copy(native.Times, times, 0, times.Length);
                return new EndfieldACLTracks(
                    values,
                    times,
                    checked((int)native.TrackCount),
                    checked((int)native.ComponentsPerTrack));
            }
            finally
            {
                DisposeDecompressedTracks(ref native);
            }
        }

        [DllImport(DLL_NAME, CallingConvention = CallingConvention.Cdecl)]
        private static extern int DecompressQvvf(
            byte[] data,
            nuint dataSize,
            ref NativeTracks tracks);

        [DllImport(DLL_NAME, CallingConvention = CallingConvention.Cdecl)]
        private static extern int DecompressFloat1f(
            byte[] data,
            nuint dataSize,
            ref NativeTracks tracks);

        [DllImport(DLL_NAME, CallingConvention = CallingConvention.Cdecl)]
        private static extern void DisposeDecompressedTracks(ref NativeTracks tracks);
    }
}
