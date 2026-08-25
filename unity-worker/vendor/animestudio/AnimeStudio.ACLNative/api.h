#pragma once

#include <cstddef>
#include <cstdint>

#if defined(_WIN32)
#define ACL_NATIVE_EXPORT extern "C" __declspec(dllexport)
#else
#define ACL_NATIVE_EXPORT extern "C" __attribute__((visibility("default")))
#endif

struct DecompressedTracks
{
    float* values;
    std::uint32_t values_count;
    float* times;
    std::uint32_t times_count;
    std::uint32_t track_count;
    std::uint32_t components_per_track;
};

ACL_NATIVE_EXPORT int DecompressQvvf(
    const std::uint8_t* data,
    std::size_t data_size,
    DecompressedTracks* output);

ACL_NATIVE_EXPORT int DecompressFloat1f(
    const std::uint8_t* data,
    std::size_t data_size,
    DecompressedTracks* output);

ACL_NATIVE_EXPORT void DisposeDecompressedTracks(DecompressedTracks* output);
