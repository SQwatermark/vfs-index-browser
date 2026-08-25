#include "api.h"

#include <cstdlib>
#include <cstring>
#include <limits>

#include "acl/core/ansi_allocator.h"
#include "acl/core/compressed_tracks.h"
#include "acl/core/impl/debug_track_writer.h"
#include "acl/decompression/decompress.h"

namespace
{
    constexpr std::uint32_t qvv_components = 10;

    void reset(DecompressedTracks* output)
    {
        if (output != nullptr)
            *output = {};
    }

    bool prepare_output(
        const acl::compressed_tracks& tracks,
        std::uint32_t components_per_track,
        DecompressedTracks* output)
    {
        const std::uint64_t sample_count = tracks.get_num_samples_per_track();
        const std::uint64_t track_count = tracks.get_num_tracks();
        const std::uint64_t value_count = sample_count * track_count * components_per_track;
        if (value_count > std::numeric_limits<std::uint32_t>::max() ||
            value_count > std::numeric_limits<std::size_t>::max() / sizeof(float) ||
            sample_count > std::numeric_limits<std::size_t>::max() / sizeof(float))
            return false;

        output->values = static_cast<float*>(std::malloc(value_count * sizeof(float)));
        output->times = static_cast<float*>(std::malloc(sample_count * sizeof(float)));
        if (output->values == nullptr || output->times == nullptr)
        {
            DisposeDecompressedTracks(output);
            return false;
        }

        output->values_count = static_cast<std::uint32_t>(value_count);
        output->times_count = static_cast<std::uint32_t>(sample_count);
        output->track_count = static_cast<std::uint32_t>(track_count);
        output->components_per_track = components_per_track;
        return true;
    }

    const acl::compressed_tracks* copy_and_validate(
        const std::uint8_t* data,
        std::size_t data_size,
        void*& aligned_data)
    {
        if (data == nullptr || data_size == 0)
            return nullptr;

#if defined(_WIN32)
        aligned_data = _aligned_malloc(data_size, 16);
#else
        aligned_data = std::aligned_alloc(16, (data_size + 15) & ~std::size_t(15));
#endif
        if (aligned_data == nullptr)
            return nullptr;

        std::memcpy(aligned_data, data, data_size);
        const auto* tracks = acl::make_compressed_tracks(aligned_data);
        return tracks->is_valid(true).empty() ? tracks : nullptr;
    }

    void free_aligned(void* data)
    {
#if defined(_WIN32)
        _aligned_free(data);
#else
        std::free(data);
#endif
    }

    float sample_time(const acl::compressed_tracks& tracks, std::uint32_t sample_index)
    {
        const float time = static_cast<float>(sample_index) / tracks.get_sample_rate();
        return time < tracks.get_duration() ? time : tracks.get_duration();
    }
}

int DecompressQvvf(
    const std::uint8_t* data,
    std::size_t data_size,
    DecompressedTracks* output)
{
    reset(output);
    if (output == nullptr)
        return 1;

    void* aligned_data = nullptr;
    const acl::compressed_tracks* tracks = copy_and_validate(data, data_size, aligned_data);
    if (tracks == nullptr || tracks->get_track_type() != acl::track_type8::qvvf)
    {
        free_aligned(aligned_data);
        return 2;
    }
    if (!prepare_output(*tracks, qvv_components, output))
    {
        free_aligned(aligned_data);
        return 3;
    }

    acl::ansi_allocator allocator;
    acl::decompression_context<acl::debug_transform_decompression_settings> context;
    context.initialize(*tracks);
    acl::acl_impl::debug_track_writer_constant_defaults writer(
        allocator,
        acl::track_type8::qvvf,
        tracks->get_num_tracks());

    for (std::uint32_t sample_index = 0; sample_index < output->times_count; ++sample_index)
    {
        const float time = sample_time(*tracks, sample_index);
        output->times[sample_index] = time;
        context.seek(time, acl::sample_rounding_policy::none);
        context.decompress_tracks(writer);

        for (std::uint32_t track_index = 0; track_index < output->track_count; ++track_index)
        {
            const rtm::qvvf& value = writer.read_qvv(track_index);
            float* target = output->values +
                ((sample_index * output->track_count + track_index) * qvv_components);
            target[0] = rtm::vector_get_x(value.translation);
            target[1] = rtm::vector_get_y(value.translation);
            target[2] = rtm::vector_get_z(value.translation);
            target[3] = rtm::quat_get_x(value.rotation);
            target[4] = rtm::quat_get_y(value.rotation);
            target[5] = rtm::quat_get_z(value.rotation);
            target[6] = rtm::quat_get_w(value.rotation);
            target[7] = rtm::vector_get_x(value.scale);
            target[8] = rtm::vector_get_y(value.scale);
            target[9] = rtm::vector_get_z(value.scale);
        }
    }

    free_aligned(aligned_data);
    return 0;
}

int DecompressFloat1f(
    const std::uint8_t* data,
    std::size_t data_size,
    DecompressedTracks* output)
{
    reset(output);
    if (output == nullptr)
        return 1;

    void* aligned_data = nullptr;
    const acl::compressed_tracks* tracks = copy_and_validate(data, data_size, aligned_data);
    if (tracks == nullptr || tracks->get_track_type() != acl::track_type8::float1f)
    {
        free_aligned(aligned_data);
        return 2;
    }
    if (!prepare_output(*tracks, 1, output))
    {
        free_aligned(aligned_data);
        return 3;
    }

    acl::ansi_allocator allocator;
    acl::decompression_context<acl::debug_scalar_decompression_settings> context;
    context.initialize(*tracks);
    acl::acl_impl::debug_track_writer writer(
        allocator,
        acl::track_type8::float1f,
        tracks->get_num_tracks());

    for (std::uint32_t sample_index = 0; sample_index < output->times_count; ++sample_index)
    {
        const float time = sample_time(*tracks, sample_index);
        output->times[sample_index] = time;
        context.seek(time, acl::sample_rounding_policy::none);
        context.decompress_tracks(writer);

        float* target = output->values + sample_index * output->track_count;
        for (std::uint32_t track_index = 0; track_index < output->track_count; ++track_index)
            target[track_index] = writer.read_float1(track_index);
    }

    free_aligned(aligned_data);
    return 0;
}

void DisposeDecompressedTracks(DecompressedTracks* output)
{
    if (output == nullptr)
        return;

    std::free(output->values);
    std::free(output->times);
    reset(output);
}
