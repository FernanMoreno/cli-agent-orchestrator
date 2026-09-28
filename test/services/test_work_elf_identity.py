"""Synthetic ELF inputs exercise the metadata parser without host binaries."""

import hashlib
import struct

import pytest

import cli_agent_orchestrator.services.work_elf_identity as work_elf_identity
from cli_agent_orchestrator.models.work_contract import ExecutableIdentity
from cli_agent_orchestrator.services.work_elf_identity import parse_elf_metadata

ET_EXEC = 2
ET_DYN = 3
ET_REL = 1
PT_NULL = 0
PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3
PF_X = 1


def make_elf(
    *,
    bits: int = 64,
    byte_order: str = "little",
    file_type: int = ET_EXEC,
    machine: int = 62,
    segments: tuple[dict, ...] | None = None,
    entry: int = 0x1000,
    include_exec_load: bool = True,
    header_overrides: dict | None = None,
) -> bytearray:
    """Build a small ELF header and program table with optional file payloads."""
    endian = "<" if byte_order == "little" else ">"
    elf_class = 1 if bits == 32 else 2
    data_encoding = 1 if byte_order == "little" else 2
    ident = b"\x7fELF" + bytes((elf_class, data_encoding, 1)) + b"\0" * 9

    if bits == 32:
        ehsize, phentsize = 52, 32
        header_format = endian + "HHIIIIIHHHHHH"
        ph_format = endian + "IIIIIIII"
    else:
        ehsize, phentsize = 64, 56
        header_format = endian + "HHIQQQIHHHHHH"
        ph_format = endian + "IIQQQQQQ"

    program_segments = list(segments or ())
    if include_exec_load and not any(segment["type"] == PT_LOAD for segment in program_segments):
        program_segments.insert(0, {"type": PT_LOAD})

    phoff = ehsize
    packed_header = bytearray(
        ident
        + struct.pack(
            header_format,
            file_type,
            machine,
            1,
            entry,
            phoff,
            0,
            0,
            ehsize,
            phentsize,
            len(program_segments),
            0,
            0,
            0,
        )
    )
    header_fields = {
        "e_type": (16, "H"),
        "e_machine": (18, "H"),
        "e_version": (20, "I"),
        "e_entry": (24, "I" if bits == 32 else "Q"),
        "e_phoff": (28 if bits == 32 else 32, "I" if bits == 32 else "Q"),
        "e_ehsize": (40 if bits == 32 else 52, "H"),
        "e_phentsize": (42 if bits == 32 else 54, "H"),
        "e_phnum": (44 if bits == 32 else 56, "H"),
    }
    for name, value in (header_overrides or {}).items():
        offset, fmt = header_fields[name]
        struct.pack_into(endian + fmt, packed_header, offset, value)

    table_end = phoff + len(program_segments) * phentsize
    payload_offset = table_end
    payloads = bytearray()
    program_headers = bytearray()
    for segment in program_segments:
        is_load = segment["type"] == PT_LOAD
        payload = segment.get("payload", b"\x90" if is_load else b"")
        offset = segment.get("offset", payload_offset if payload else 0)
        vaddr = segment.get("vaddr", 0x1000 if is_load else 0)
        filesz = segment.get("filesz", len(payload))
        memsz = segment.get("memsz", filesz)
        flags = segment.get("flags", PF_X if is_load else 0)
        align = segment.get("align", 1)
        if bits == 32:
            fields = (
                segment["type"],
                offset,
                vaddr,
                0,
                filesz,
                memsz,
                flags,
                align,
            )
        else:
            fields = (
                segment["type"],
                flags,
                offset,
                vaddr,
                0,
                filesz,
                memsz,
                align,
            )
        program_headers.extend(struct.pack(ph_format, *fields))
        if payload:
            payloads.extend(payload)
            payload_offset += len(payload)

    return packed_header + program_headers + payloads


def make_dynamic_elf(
    interpreter_path: bytes = b"/lib/ld.so\0",
    *,
    bits: int = 64,
    byte_order: str = "little",
    machine: int = 62,
) -> bytearray:
    return make_elf(
        bits=bits,
        byte_order=byte_order,
        file_type=ET_DYN,
        machine=machine,
        segments=(
            {"type": PT_INTERP, "payload": interpreter_path},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        ),
    )


def identify(*args, **kwargs):
    builder = getattr(work_elf_identity, "identify_static_executable", None)
    assert callable(builder), "identify_static_executable must expose static identity parsing"
    return builder(*args, **kwargs)


@pytest.mark.parametrize(
    ("bits", "byte_order", "expected_class", "expected_endianness"),
    [
        (32, "little", 32, "little"),
        (32, "big", 32, "big"),
        (64, "little", 64, "little"),
        (64, "big", 64, "big"),
    ],
)
def test_reports_elf_class_machine_and_byte_order(
    bits, byte_order, expected_class, expected_endianness
):
    metadata = parse_elf_metadata(make_elf(bits=bits, byte_order=byte_order))

    assert metadata.elf_class == expected_class
    assert metadata.endianness == expected_endianness
    assert metadata.machine == 62
    assert metadata.file_type == ET_EXEC
    assert metadata.entry_point == 0x1000
    assert metadata.is_static is True
    assert metadata.interpreter_path is None


@pytest.mark.parametrize("machine", [3, 40, 62, 183])
def test_parser_reports_machine_number_for_diagnostics(machine):
    metadata = parse_elf_metadata(make_elf(machine=machine))

    assert metadata.machine == machine


def test_rejects_pt_null_only_executable():
    data = make_elf(segments=({"type": PT_NULL},), include_exec_load=False)
    assert len(data) == 120

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_reports_dynamic_elf_with_canonical_interpreter():
    data = make_elf(
        file_type=ET_DYN,
        segments=(
            {"type": PT_LOAD},
            {"type": PT_INTERP, "payload": b"/lib64/ld-linux-x86-64.so.2\0"},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        ),
    )

    metadata = parse_elf_metadata(data)

    assert metadata.is_static is False
    assert metadata.interpreter_path == "/lib64/ld-linux-x86-64.so.2"


def test_identifies_static_executable_and_hashes_the_supplied_bytes():
    executable_bytes = make_elf(machine=62)
    expected_digest = hashlib.sha256(executable_bytes).hexdigest()

    identity = identify("/usr/bin/example", executable_bytes)

    assert isinstance(identity, ExecutableIdentity)
    assert identity.command_token == "/usr/bin/example"
    assert identity.content_reference == f"sha256:{expected_digest}"
    assert identity.sha256_digest == expected_digest
    assert identity.elf_machine == "x86_64"
    assert identity.elf_class == "ELF64"
    assert identity.endianness == "little"
    assert identity.static is True


@pytest.mark.parametrize(
    ("machine", "bits", "byte_order"),
    [
        (62, 32, "little"),
        (3, 32, "little"),
        (40, 32, "little"),
        (183, 64, "little"),
        (40, 32, "big"),
        (62, 64, "big"),
        (183, 64, "big"),
    ],
    ids=[
        "x86_64-elf32",
        "i386",
        "arm",
        "aarch64",
        "arm-big-endian",
        "x86_64-big-endian",
        "aarch64-big-endian",
    ],
)
def test_rejects_builder_architecture_outside_seccomp_profile(machine, bits, byte_order):
    with pytest.raises(ValueError, match="only x86_64/ELF64/little static ELF is supported"):
        identify("/usr/bin/example", make_elf(machine=machine, bits=bits, byte_order=byte_order))


def test_rejects_dynamic_executable_with_interpreter_metadata():
    with pytest.raises(ValueError):
        identify("/usr/bin/example", make_dynamic_elf())


def test_rejects_loader_bytes_argument_for_static_builder():
    with pytest.raises(TypeError):
        identify("/usr/bin/example", make_dynamic_elf(), interpreter_bytes=make_elf())


def test_rejects_static_pie_with_pt_dynamic():
    static_pie = make_elf(
        file_type=ET_DYN,
        segments=({"type": PT_DYNAMIC, "payload": b"\0" * 16},),
    )

    with pytest.raises(ValueError):
        identify("/usr/bin/example", static_pie)


def test_rejects_unsupported_elf_machine():
    with pytest.raises(ValueError):
        identify("/usr/bin/example", make_elf(machine=999))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x7fELF" + b"\0" * 11,
        b"\x7fELF" + bytes((3, 1, 1)) + b"\0" * 9,
        b"\x7fELF" + bytes((2, 3, 1)) + b"\0" * 9,
        b"\x7fELF" + bytes((2, 1, 0)) + b"\0" * 9,
    ],
    ids=["empty", "truncated-ident", "unknown-class", "unknown-byte-order", "bad-ident-version"],
)
def test_rejects_truncated_or_unsupported_elf_identification(data):
    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_truncated_elf_header():
    data = make_elf(bits=64)[:63]

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_unsupported_file_type():
    with pytest.raises(ValueError):
        parse_elf_metadata(make_elf(file_type=ET_REL))


@pytest.mark.parametrize(
    "overrides",
    [{"e_version": 2}, {"e_phnum": 0}],
    ids=["bad-header-version", "empty-program-table"],
)
def test_rejects_invalid_elf_version_or_missing_program_headers(overrides):
    with pytest.raises(ValueError):
        parse_elf_metadata(make_elf(header_overrides=overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"e_ehsize": 63},
        {"e_phentsize": 55},
        {"e_phentsize": 57},
        {"e_phoff": 63},
        {"e_phoff": 4096},
    ],
    ids=["bad-ehsize", "short-phdr", "long-phdr", "phdr-overlaps-header", "phdr-out-of-file"],
)
def test_rejects_invalid_header_and_program_table_bounds(overrides):
    with pytest.raises(ValueError):
        parse_elf_metadata(make_elf(header_overrides=overrides))


def test_rejects_program_header_table_truncated_by_file_end():
    data = make_elf(segments=({"type": PT_NULL}, {"type": PT_NULL}), include_exec_load=False)
    del data[-1:]

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


@pytest.mark.parametrize(
    "interpreter",
    [
        b"relative/ld.so\0",
        b"/lib/./ld.so\0",
        b"/lib/../ld.so\0",
        b"/lib//ld.so\0",
        b"/lib/ld.so/\0",
        b"/lib\\ld.so\0",
        b"/lib/ld.so\x00extra\0",
        b"/lib/ld-\xff.so\0",
        b"/lib/ld.so",
        b"\0",
        b"/\0",
        b"/lib/ld\x7f.so\0",
    ],
    ids=[
        "relative",
        "dot-component",
        "dotdot-component",
        "repeated-separator",
        "trailing-separator",
        "backslash",
        "embedded-nul",
        "non-utf8",
        "missing-terminator",
        "empty-interpreter",
        "root-is-not-an-interpreter",
        "control-character",
    ],
)
def test_rejects_malformed_or_noncanonical_interpreter_paths(interpreter):
    data = make_elf(
        segments=(
            {"type": PT_INTERP, "payload": interpreter},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        )
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_duplicate_interpreter_segments():
    data = make_elf(
        segments=(
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0"},
            {"type": PT_INTERP, "payload": b"/lib/other-ld.so\0"},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        )
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_duplicate_dynamic_segments():
    data = make_elf(
        segments=(
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0"},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        )
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_dynamic_segment_without_interpreter():
    data = make_elf(segments=({"type": PT_DYNAMIC, "payload": b"\0" * 16},))

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_interpreter_without_dynamic_segment():
    data = make_elf(segments=({"type": PT_INTERP, "payload": b"/lib/ld.so\0"},))

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_interpreter_payload_outside_file_bounds():
    data = make_elf(
        segments=(
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0", "offset": 4096},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        )
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_rejects_dynamic_payload_outside_file_bounds():
    data = make_elf(
        segments=(
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0"},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16, "offset": 4096},
        )
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


@pytest.mark.parametrize(
    ("interpreter_segment", "dynamic_segment"),
    [
        (
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0", "memsz": 1},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16},
        ),
        (
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0"},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16, "memsz": 1},
        ),
        (
            {"type": PT_INTERP, "payload": b"/lib/ld.so\0"},
            {"type": PT_DYNAMIC, "payload": b"\0" * 16, "filesz": 4096},
        ),
    ],
    ids=[
        "interpreter-too-small-memory-size",
        "dynamic-too-small-memory-size",
        "dynamic-too-long-file-size",
    ],
)
def test_rejects_malformed_loader_segment_lengths(interpreter_segment, dynamic_segment):
    data = make_elf(segments=(interpreter_segment, dynamic_segment))

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_accepts_executable_load_with_valid_power_of_two_alignment():
    data = make_elf(
        segments=(
            {
                "type": PT_LOAD,
                "payload": b"\x90",
                "flags": PF_X,
                "vaddr": 0x1008,
                "align": 16,
            },
        ),
        entry=0x1008,
    )

    metadata = parse_elf_metadata(data)

    assert metadata.entry_point == 0x1008


def test_rejects_pt_load_without_executable_flag():
    data = make_elf(
        segments=({"type": PT_LOAD, "flags": 0},),
        include_exec_load=False,
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


def test_accepts_non_executable_bss_only_pt_load():
    data = make_elf(
        segments=(
            {"type": PT_LOAD, "payload": b"\x90", "flags": PF_X, "vaddr": 0x1000},
            {
                "type": PT_LOAD,
                "payload": b"",
                "filesz": 0,
                "memsz": 0x100,
                "flags": 0,
                "vaddr": 0x2000,
            },
        ),
    )

    metadata = parse_elf_metadata(data)

    assert metadata.entry_point == 0x1000
    assert metadata.is_static is True


@pytest.mark.parametrize(
    "segment",
    [
        {"type": PT_LOAD, "payload": b"\x90\x90", "memsz": 1},
        {"type": PT_LOAD, "payload": b"\x90", "filesz": 0, "memsz": 0},
        {"type": PT_LOAD, "payload": b"\x90", "offset": 4096},
        {"type": PT_LOAD, "payload": b"\x90", "vaddr": 0xFFFFFFFE, "memsz": 4},
    ],
    ids=[
        "memsz-smaller-than-filesz",
        "zero-filesz",
        "file-range-out-of-bounds",
        "virtual-range-overflow",
    ],
)
def test_rejects_pt_load_with_invalid_bounds(segment):
    data = make_elf(bits=32, machine=3, segments=(segment,))

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


@pytest.mark.parametrize(
    "segment",
    [
        {"type": PT_LOAD, "payload": b"\x90", "align": 3, "vaddr": 0x1002},
        {"type": PT_LOAD, "payload": b"\x90", "align": 16, "vaddr": 0x1000},
    ],
    ids=["non-power-of-two-alignment", "offset-vaddr-not-congruent"],
)
def test_rejects_pt_load_with_invalid_alignment(segment):
    data = make_elf(segments=(segment,))

    with pytest.raises(ValueError):
        parse_elf_metadata(data)


@pytest.mark.parametrize("entry", [0x2000, 0x1001, 0x1004])
def test_rejects_entry_outside_executable_file_backed_load_bytes(entry):
    data = make_elf(
        segments=({"type": PT_LOAD, "payload": b"\x90", "memsz": 8},),
        entry=entry,
    )

    with pytest.raises(ValueError):
        parse_elf_metadata(data)
