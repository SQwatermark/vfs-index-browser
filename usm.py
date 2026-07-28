from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class UsmError(Exception):
    pass


CRID = b"CRID"
SFV = b"@SFV"
SFA = b"@SFA"
ALP = b"@ALP"
SBT = b"@SBT"
CUE = b"@CUE"
UTF = b"@UTF"
KNOWN_BLOCKS = {CRID, SFV, SFA, ALP, SBT, CUE, UTF}

HEADER_END = b"#HEADER END     ===============\x00"
METADATA_END = b"#METADATA END   ===============\x00"
CONTENTS_END = b"#CONTENTS END   ===============\x00"


def convert_usm_to_mp4(
    data: bytes,
    output_path: Path,
    *,
    usm_convert: Path | None = None,
    ffmpeg: str | Path = "ffmpeg",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if usm_convert and usm_convert.exists() and _try_usm_convert(data, output_path, usm_convert):
        return

    streams = _demux_usm(data)
    _mux_to_mp4(streams, output_path, ffmpeg)


def _try_usm_convert(data: bytes, output_path: Path, helper: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="endfield_usm_helper_") as temp:
        temp_dir = Path(temp)
        input_path = temp_dir / "input.usm"
        output_dir = temp_dir / "out"
        helper_output = output_dir / "input.mp4"
        output_dir.mkdir()
        input_path.write_bytes(data)
        try:
            result = subprocess.run(
                [str(helper), "-o", str(output_dir), str(input_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            return False
        if result.returncode != 0 or not helper_output.exists():
            return False
        shutil.copyfile(helper_output, output_path)
        return True


def _demux_usm(data: bytes) -> dict[str, bytes | str | None]:
    offset = data.find(CRID)
    if offset < 0:
        raise UsmError("invalid USM data: CRID marker not found")

    video_streams: dict[int, bytearray] = {}
    audio_streams: dict[int, bytearray] = {}
    while offset + 8 <= len(data):
        block_id = data[offset : offset + 4]
        if block_id not in KNOWN_BLOCKS:
            break
        block_size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        block_end = offset + 8 + block_size
        if block_end > len(data):
            break

        is_video = block_id == SFV
        is_audio = block_id == SFA
        if (is_video or is_audio) and offset + 0xE <= len(data):
            header_size = int.from_bytes(data[offset + 8 : offset + 0xA], "big")
            footer_size = int.from_bytes(data[offset + 0xA : offset + 0xC], "big")
            if block_size > header_size + footer_size:
                payload_start = offset + 8 + header_size
                payload_size = block_size - header_size - footer_size
                payload_end = payload_start + payload_size
                if payload_end <= len(data):
                    if is_audio:
                        stream_id = data[offset + 0xC]
                        target = audio_streams.setdefault(stream_id, bytearray())
                    else:
                        target = video_streams.setdefault(0, bytearray())
                    target.extend(data[payload_start:payload_end])

        offset = block_end

    if not video_streams:
        raise UsmError("no video stream found")

    video = _strip_markers(bytes(next(iter(video_streams.values()))))
    audio = _strip_markers(bytes(next(iter(audio_streams.values())))) if audio_streams else None
    return {
        "video": video,
        "audio": audio,
        "audio_extension": _detect_audio_extension(audio) if audio else None,
    }


def _strip_markers(data: bytes) -> bytes:
    header_end = data.find(HEADER_END)
    metadata_end = data.find(METADATA_END)
    header_size = 0
    if header_end >= 0 and metadata_end >= 0:
        header_size = max(header_end + len(HEADER_END), metadata_end + len(METADATA_END))
    elif header_end >= 0:
        header_size = header_end + len(HEADER_END)
    elif metadata_end >= 0:
        header_size = metadata_end + len(METADATA_END)

    start = min(header_size, len(data))
    footer = data.find(CONTENTS_END, start)
    end = footer if footer >= 0 else len(data)
    return data[start:end]


def _detect_audio_extension(data: bytes | None) -> str:
    if not data or len(data) < 4:
        return ".bin"
    if data.startswith(b"AIXF"):
        return ".aix"
    if data[0] == 0x80:
        return ".adx"
    if data[:4] == b"HCA\x00":
        return ".hca"
    return ".bin"


def _mux_to_mp4(streams: dict[str, bytes | str | None], output_path: Path, ffmpeg: str | Path) -> None:
    with tempfile.TemporaryDirectory(prefix="endfield_usm_") as temp:
        temp_dir = Path(temp)
        video_path = temp_dir / "video.m2v"
        video_path.write_bytes(streams["video"] or b"")

        command = [
            str(ffmpeg),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
        ]

        audio_path = None
        audio = streams.get("audio")
        if isinstance(audio, bytes) and audio:
            audio_extension = str(streams.get("audio_extension") or ".bin")
            audio_path = temp_dir / f"audio{audio_extension}"
            audio_path.write_bytes(audio)
            command.extend(["-i", str(audio_path)])

        command.extend(["-c", "copy", "-video_track_timescale", "90000", str(output_path)])
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except OSError as error:
            raise UsmError(f"ffmpeg is not available: {error}") from error
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise UsmError(f"ffmpeg remux failed: {stderr or result.returncode}")
