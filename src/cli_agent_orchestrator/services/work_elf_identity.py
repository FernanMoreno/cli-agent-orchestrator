"""Strict, dependency-free parsing of executable ELF identity metadata."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from cli_agent_orchestrator.models.work_contract import ExecutableIdentity

_ELF_MAGIC = b"\x7fELF"
_ET_EXEC = 2
_ET_DYN = 3
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3
_PF_X = 1
_EV_CURRENT = 1
_MACHINES = {
    62: "x86_64",
}
_ELF_CLASSES = {64: "ELF64"}
_SUPPORTED_IDENTITY_ELF = (62, 64, "little")


@dataclass(frozen=True, slots=True)
class ElfMetadata:
    """Validated architecture and loader metadata from an ELF executable."""

    elf_class: int
    endianness: str
    machine: int
    file_type: int
    entry_point: int
    is_static: bool
    interpreter_path: str | None


def parse_elf_metadata(data: bytes) -> ElfMetadata:
    """Parse an ELF32/ELF64 header and its program headers.

    Only ET_EXEC and ET_DYN files are accepted. A PT_DYNAMIC segment must be
    paired with exactly one valid PT_INTERP segment; this keeps ambiguous
    static/dynamic classifications out of callers' hands.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("ELF input must be bytes")
    raw = bytes(data)
    if len(raw) < 16 or raw[:4] != _ELF_MAGIC:
        raise ValueError("invalid or truncated ELF identification")

    elf_class_id = raw[4]
    if elf_class_id not in (1, 2):
        raise ValueError("unsupported ELF class")
    elf_class = 32 if elf_class_id == 1 else 64

    data_encoding = raw[5]
    if data_encoding not in (1, 2):
        raise ValueError("unsupported ELF byte order")
    endianness = "little" if data_encoding == 1 else "big"
    endian = "<" if data_encoding == 1 else ">"
    if raw[6] != _EV_CURRENT:
        raise ValueError("unsupported ELF identification version")

    if elf_class == 32:
        header_size = 52
        program_header_size = 32
        header_format = endian + "HHIIIIIHHHHHH"
        program_header_format = endian + "IIIIIIII"
    else:
        header_size = 64
        program_header_size = 56
        header_format = endian + "HHIQQQIHHHHHH"
        program_header_format = endian + "IIQQQQQQ"

    if len(raw) < header_size:
        raise ValueError("truncated ELF header")

    (
        file_type,
        machine,
        version,
        entry_point,
        phoff,
        _shoff,
        _flags,
        ehsize,
        phentsize,
        phnum,
        _shentsize,
        _shnum,
        _shstrndx,
    ) = struct.unpack_from(header_format, raw, 16)

    if file_type not in (_ET_EXEC, _ET_DYN):
        raise ValueError("unsupported ELF file type")
    if version != _EV_CURRENT:
        raise ValueError("unsupported ELF header version")
    if ehsize != header_size:
        raise ValueError("invalid ELF header size")
    if phentsize != program_header_size:
        raise ValueError("invalid ELF program-header entry size")
    if phnum == 0:
        raise ValueError("ELF executable has no program headers")
    if phoff < header_size:
        raise ValueError("ELF program-header table overlaps the ELF header")
    if phoff > len(raw) or phnum > (len(raw) - phoff) // phentsize:
        raise ValueError("truncated ELF program-header table")

    interpreter_paths: list[str] = []
    executable_load_ranges: list[tuple[int, int]] = []
    dynamic_segments = 0
    address_space = 1 << elf_class
    for index in range(phnum):
        offset = phoff + index * phentsize
        fields = struct.unpack_from(program_header_format, raw, offset)
        if elf_class == 32:
            (
                segment_type,
                file_offset,
                virtual_address,
                _paddr,
                file_size,
                memory_size,
                flags,
                align,
            ) = fields
        else:
            (
                segment_type,
                flags,
                file_offset,
                virtual_address,
                _paddr,
                file_size,
                memory_size,
                align,
            ) = fields

        if file_offset > len(raw) or file_size > len(raw) - file_offset:
            raise ValueError("ELF segment lies outside the file")

        if segment_type == _PT_LOAD:
            if memory_size < file_size:
                raise ValueError("PT_LOAD memory size cannot be smaller than file size")
            if file_offset > address_space - file_size:
                raise ValueError("PT_LOAD file range overflows the ELF address width")
            if virtual_address > address_space - memory_size:
                raise ValueError("PT_LOAD virtual range overflows the ELF address width")
            if align not in (0, 1):
                if align & (align - 1):
                    raise ValueError("PT_LOAD alignment must be zero, one, or a power of two")
                if file_offset % align != virtual_address % align:
                    raise ValueError("PT_LOAD file and virtual addresses are not congruent")
            if flags & _PF_X:
                if file_size == 0:
                    raise ValueError("executable PT_LOAD segments must contain file-backed bytes")
                executable_load_ranges.append((virtual_address, virtual_address + file_size))
        elif segment_type == _PT_INTERP:
            if len(interpreter_paths) >= 1:
                raise ValueError("duplicate PT_INTERP segments")
            if file_size == 0 or memory_size < file_size:
                raise ValueError("invalid PT_INTERP segment length")
            interpreter_paths.append(
                _decode_interpreter(raw[file_offset : file_offset + file_size])
            )
        elif segment_type == _PT_DYNAMIC:
            dynamic_segments += 1
            if dynamic_segments > 1:
                raise ValueError("duplicate PT_DYNAMIC segments")
            if file_size == 0 or memory_size < file_size:
                raise ValueError("invalid PT_DYNAMIC segment length")

    if not executable_load_ranges:
        raise ValueError("ELF executable requires a PT_LOAD segment with PF_X")
    if not any(start <= entry_point < end for start, end in executable_load_ranges):
        raise ValueError("ELF entry point must lie in executable file-backed PT_LOAD bytes")

    interpreter_path = interpreter_paths[0] if interpreter_paths else None
    if bool(dynamic_segments) != (interpreter_path is not None):
        raise ValueError("PT_INTERP and PT_DYNAMIC must appear together")

    return ElfMetadata(
        elf_class=elf_class,
        endianness=endianness,
        machine=machine,
        file_type=file_type,
        entry_point=entry_point,
        is_static=dynamic_segments == 0 and interpreter_path is None,
        interpreter_path=interpreter_path,
    )


def identify_static_executable(command_token: str, executable_bytes: bytes) -> ExecutableIdentity:
    """Build identity only for an ELF image without dynamic loader metadata."""
    executable_snapshot = _snapshot_bytes(executable_bytes)
    executable_metadata = parse_elf_metadata(executable_snapshot)
    if not executable_metadata.is_static or executable_metadata.interpreter_path is not None:
        raise ValueError("only ELF without PT_DYNAMIC or PT_INTERP can have executable identity")
    _require_supported_identity_elf(executable_metadata)
    return _make_static_identity(command_token, executable_snapshot, executable_metadata)


def _snapshot_bytes(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("ELF input must be bytes")
    return bytes(data)


def _make_static_identity(
    command_token: str, data: bytes, metadata: ElfMetadata
) -> ExecutableIdentity:
    if not metadata.is_static or metadata.interpreter_path is not None:
        raise ValueError("executable identity builder requires static ELF metadata")
    _require_supported_identity_elf(metadata)
    machine = _MACHINES.get(metadata.machine)
    elf_class = _ELF_CLASSES.get(metadata.elf_class)
    if machine is None:
        raise ValueError(f"unsupported ELF machine: {metadata.machine}")
    if elf_class is None:
        raise ValueError(f"unsupported ELF class: {metadata.elf_class}")

    digest = hashlib.sha256(data).hexdigest()
    return ExecutableIdentity(
        command_token=command_token,
        content_reference=f"sha256:{digest}",
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )


def _require_supported_identity_elf(metadata: ElfMetadata) -> None:
    if (metadata.machine, metadata.elf_class, metadata.endianness) != _SUPPORTED_IDENTITY_ELF:
        raise ValueError("only x86_64/ELF64/little static ELF is supported")


def _decode_interpreter(segment: bytes) -> str:
    if not segment.endswith(b"\0") or b"\0" in segment[:-1]:
        raise ValueError("PT_INTERP must contain one trailing NUL byte")
    try:
        path = segment[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("PT_INTERP path is not valid UTF-8") from error

    if not path.startswith("/") or path == "/" or "\\" in path:
        raise ValueError("PT_INTERP path must be a canonical absolute path")
    components = path[1:].split("/")
    if any(component in ("", ".", "..") for component in components):
        raise ValueError("PT_INTERP path must be a canonical absolute path")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in path):
        raise ValueError("PT_INTERP path contains a control character")
    return path
