"""Read and write Smash Ultimate SHAN / TPCB (.shpcanim) files.

Layout is from the ultimate-research/shpc crate. Coefficients are L0+L1
spherical harmonics, 4 bytes per RGB channel, decompressed with unk5/unk6.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Optional


SH_MIN = (0.1481, -0.2962, -0.08551, 0.35544)
SH_SCALE = (0.32573469, 0.32573469, 0.32573469, 0.28209451)


def decompress_coefficients(unk5: float, unk6: float, compressed) -> list[float]:
    t = (
        float(compressed[3]),
        float(compressed[2]),
        float(compressed[1]),
        float(compressed[0]),
    )
    result = []
    for i in range(4):
        min_value = SH_MIN[i] + SH_SCALE[i] * unk5
        scale = SH_SCALE[i] * unk6
        result.append(t[i] * scale + min_value)
    return result


def compress_coefficients(unk5: float, unk6: float, coefficients) -> list[int]:
    if unk6 == 0.0:
        return [0, 0, 0, 0]
    buffer = []
    for i in range(4):
        min_value = SH_MIN[i] + SH_SCALE[i] * unk5
        scale = SH_SCALE[i] * unk6
        buffer.append((float(coefficients[i]) - min_value) / scale)
    rounded = [max(0, min(255, int(round(v)))) for v in buffer]
    return [rounded[3], rounded[2], rounded[1], rounded[0]]


@dataclass
class ShCell:
    r: list[float]
    g: list[float]
    b: list[float]
    r_raw: list[int]
    g_raw: list[int]
    b_raw: list[int]


@dataclass
class Tpcb:
    file_offset: int
    coeff_file_offset: int
    unk1_1: int
    unk1_2: int
    grid_cell_count_xyz: tuple[int, int, int]
    grid_spacing_xyz: tuple[float, float, float]
    grid_dimensions_xyz: tuple[float, float, float]
    grid_range_min_xyz: tuple[float, float, float]
    grid_range_max_xyz: tuple[float, float, float]
    unk4: int
    unk5: float
    unk6: float
    grid_cell_count: int
    grid_indices: Optional[list[int]]
    cells: list[ShCell]
    grid_unk_values: Optional[list[tuple[float, float, float]]]


@dataclass
class Shan:
    unk1: int
    unk3: int
    name: str
    starting_frames: list[int]
    tpcbs: list[Tpcb] = field(default_factory=list)
    original_bytes: bytes = b""


def _read_tpcb(data: bytes, start: int) -> Tpcb:
    if data[start:start + 4] != b"TPCB":
        raise ValueError(f"Expected TPCB magic at offset {start}")
    offset1, offset2, offset3 = struct.unpack_from("<III", data, start + 4)
    header = start + 16
    unk1_1, unk1_2 = struct.unpack_from("<HH", data, header)
    count_xyz = struct.unpack_from("<III", data, header + 4)
    spacing = struct.unpack_from("<fff", data, header + 16)
    dimensions = struct.unpack_from("<fff", data, header + 28)
    range_min = struct.unpack_from("<fff", data, header + 40)
    range_max = struct.unpack_from("<fff", data, header + 52)
    unk4 = struct.unpack_from("<I", data, header + 64)[0]
    unk5 = struct.unpack_from("<f", data, header + 68)[0]
    unk6 = struct.unpack_from("<f", data, header + 72)[0]
    cell_count = struct.unpack_from("<I", data, header + 76)[0]

    indices = None
    if offset1 > 0:
        indices = list(struct.unpack_from(f"<{cell_count}H", data, start + offset1))

    cells: list[ShCell] = []
    coeff_offset = start + offset2 if offset2 > 0 else 0
    if offset2 > 0:
        pos = coeff_offset
        for _ in range(cell_count):
            raw = data[pos:pos + 12]
            if len(raw) < 12:
                raise ValueError("Truncated SH coefficient block")
            r_raw = list(raw[0:4])
            g_raw = list(raw[4:8])
            b_raw = list(raw[8:12])
            cells.append(ShCell(
                r=decompress_coefficients(unk5, unk6, r_raw),
                g=decompress_coefficients(unk5, unk6, g_raw),
                b=decompress_coefficients(unk5, unk6, b_raw),
                r_raw=r_raw,
                g_raw=g_raw,
                b_raw=b_raw,
            ))
            pos += 12

    unk_values = None
    if offset3 > 0:
        unk_values = []
        pos = start + offset3
        for _ in range(cell_count):
            unk_values.append(struct.unpack_from("<fff", data, pos))
            pos += 12

    return Tpcb(
        file_offset=start,
        coeff_file_offset=coeff_offset,
        unk1_1=unk1_1,
        unk1_2=unk1_2,
        grid_cell_count_xyz=count_xyz,
        grid_spacing_xyz=spacing,
        grid_dimensions_xyz=dimensions,
        grid_range_min_xyz=range_min,
        grid_range_max_xyz=range_max,
        unk4=unk4,
        unk5=unk5,
        unk6=unk6,
        grid_cell_count=cell_count,
        grid_indices=indices,
        cells=cells,
        grid_unk_values=unk_values,
    )


def read_shpcanim(path: str) -> Shan:
    with open(path, "rb") as handle:
        data = handle.read()
    return parse_shpcanim(data)


def parse_shpcanim(data: bytes) -> Shan:
    if data[0:4] != b"SHAN":
        raise ValueError("Not a SHAN / shpcanim file")
    unk1, tpcb_count, unk3 = struct.unpack_from("<III", data, 4)
    name_len = struct.unpack_from("<I", data, 16)[0]
    name = data[20:20 + name_len].decode("utf-8", errors="replace")

    offset = 128
    starting_frames = list(struct.unpack_from(f"<{tpcb_count}I", data, offset)) if tpcb_count else []
    offset += 4 * tpcb_count
    tpcb_ptrs = list(struct.unpack_from(f"<{tpcb_count}I", data, offset)) if tpcb_count else []

    tpcbs = [_read_tpcb(data, ptr) for ptr in tpcb_ptrs]
    return Shan(
        unk1=unk1,
        unk3=unk3,
        name=name,
        starting_frames=starting_frames,
        tpcbs=tpcbs,
        original_bytes=data,
    )


def _write_tpcb(tpcb: Tpcb) -> bytes:
    count = tpcb.grid_cell_count
    has_unk = tpcb.grid_unk_values is not None
    offset1 = 96
    offset2 = offset1 + count * 2
    offset3 = (offset2 + count * 12) if has_unk else 0

    buf = bytearray()
    buf += b"TPCB"
    buf += struct.pack("<III", offset1, offset2, offset3)
    buf += struct.pack("<HH", tpcb.unk1_1, tpcb.unk1_2)
    buf += struct.pack("<III", *tpcb.grid_cell_count_xyz)
    buf += struct.pack("<fff", *tpcb.grid_spacing_xyz)
    buf += struct.pack("<fff", *tpcb.grid_dimensions_xyz)
    buf += struct.pack("<fff", *tpcb.grid_range_min_xyz)
    buf += struct.pack("<fff", *tpcb.grid_range_max_xyz)
    buf += struct.pack("<I", tpcb.unk4)
    buf += struct.pack("<f", tpcb.unk5)
    buf += struct.pack("<f", tpcb.unk6)
    buf += struct.pack("<I", count)

    if tpcb.grid_indices is not None:
        buf += struct.pack(f"<{count}H", *tpcb.grid_indices)
    else:
        buf += struct.pack(f"<{count}H", *range(count))

    for cell in tpcb.cells:
        r = compress_coefficients(tpcb.unk5, tpcb.unk6, cell.r)
        g = compress_coefficients(tpcb.unk5, tpcb.unk6, cell.g)
        b = compress_coefficients(tpcb.unk5, tpcb.unk6, cell.b)
        buf += bytes(r + g + b)

    if has_unk:
        for value in tpcb.grid_unk_values:
            buf += struct.pack("<fff", *value)
    return bytes(buf)


def write_shpcanim_bytes(shan: Shan) -> bytes:
    if shan.original_bytes and _can_patch(shan):
        return _patch_original(shan)

    name_bytes = shan.name.encode("utf-8")
    buf = bytearray()
    buf += b"SHAN"
    buf += struct.pack("<III", shan.unk1, len(shan.tpcbs), shan.unk3)
    buf += struct.pack("<I", len(name_bytes))
    buf += name_bytes
    if len(buf) < 128:
        buf += b"\x00" * (128 - len(buf))

    for frame in shan.starting_frames:
        buf += struct.pack("<I", frame)

    ptr_offset = len(buf)
    buf += b"\x00" * (4 * len(shan.tpcbs))

    pointers = []
    for tpcb in shan.tpcbs:
        while len(buf) % 16 != 0:
            buf += b"\x00"
        pointers.append(len(buf))
        buf += _write_tpcb(tpcb)

    for index, pointer in enumerate(pointers):
        struct.pack_into("<I", buf, ptr_offset + index * 4, pointer)
    return bytes(buf)


def write_shpcanim(path: str, shan: Shan) -> None:
    with open(path, "wb") as handle:
        handle.write(write_shpcanim_bytes(shan))


def _can_patch(shan: Shan) -> bool:
    data = shan.original_bytes
    if not data:
        return False
    for tpcb in shan.tpcbs:
        end = tpcb.coeff_file_offset + tpcb.grid_cell_count * 12
        if tpcb.coeff_file_offset <= 0 or end > len(data):
            return False
        if len(tpcb.cells) != tpcb.grid_cell_count:
            return False
    return True


def _patch_original(shan: Shan) -> bytes:
    data = bytearray(shan.original_bytes)
    for tpcb in shan.tpcbs:
        pos = tpcb.coeff_file_offset
        for cell in tpcb.cells:
            r = compress_coefficients(tpcb.unk5, tpcb.unk6, cell.r)
            g = compress_coefficients(tpcb.unk5, tpcb.unk6, cell.g)
            b = compress_coefficients(tpcb.unk5, tpcb.unk6, cell.b)
            data[pos:pos + 12] = bytes(r + g + b)
            pos += 12
    return bytes(data)


def scale_tpcb_cells(tpcb: Tpcb, intensity: float, tint) -> None:
    tr, tg, tb = float(tint[0]), float(tint[1]), float(tint[2])
    for cell in tpcb.cells:
        cell.r = [c * intensity * tr for c in cell.r]
        cell.g = [c * intensity * tg for c in cell.g]
        cell.b = [c * intensity * tb for c in cell.b]


def tpcb_for_frame(shan: Shan, frame: float) -> tuple[int, Tpcb]:
    if not shan.tpcbs:
        raise ValueError("SHPC file has no TPCB blocks")
    chosen = 0
    for index, start in enumerate(shan.starting_frames):
        if frame >= start:
            chosen = index
    return chosen, shan.tpcbs[chosen]


def cell_position_smash(tpcb: Tpcb, index: int) -> tuple[float, float, float]:
    nx, ny, nz = tpcb.grid_cell_count_xyz
    if nx <= 0:
        nx = 1
    if ny <= 0:
        ny = 1
    if nz <= 0:
        nz = 1
    x = index % nx
    y = (index // nx) % ny
    z = index // (nx * ny)
    spacing = tpcb.grid_spacing_xyz
    origin = tpcb.grid_range_min_xyz
    return (
        origin[0] + x * spacing[0],
        origin[1] + y * spacing[1],
        origin[2] + z * spacing[2],
    )


def smash_to_blender(pos) -> tuple[float, float, float]:
    return (float(pos[0]), -float(pos[2]), float(pos[1]))


def cell_l0_color(cell: ShCell) -> tuple[float, float, float]:
    return (cell.r[3], cell.g[3], cell.b[3])


def shan_to_json(shan: Shan) -> str:
    payload = {
        "unk1": shan.unk1,
        "unk3": shan.unk3,
        "name": shan.name,
        "starting_frames": shan.starting_frames,
        "tpcbs": [],
    }
    for tpcb in shan.tpcbs:
        payload["tpcbs"].append({
            "file_offset": tpcb.file_offset,
            "coeff_file_offset": tpcb.coeff_file_offset,
            "unk1_1": tpcb.unk1_1,
            "unk1_2": tpcb.unk1_2,
            "grid_cell_count_xyz": list(tpcb.grid_cell_count_xyz),
            "grid_spacing_xyz": list(tpcb.grid_spacing_xyz),
            "grid_dimensions_xyz": list(tpcb.grid_dimensions_xyz),
            "grid_range_min_xyz": list(tpcb.grid_range_min_xyz),
            "grid_range_max_xyz": list(tpcb.grid_range_max_xyz),
            "unk4": tpcb.unk4,
            "unk5": tpcb.unk5,
            "unk6": tpcb.unk6,
            "grid_cell_count": tpcb.grid_cell_count,
            "grid_indices": tpcb.grid_indices,
            "grid_unk_values": [list(v) for v in tpcb.grid_unk_values] if tpcb.grid_unk_values else None,
            "cells": [
                {
                    "r": cell.r,
                    "g": cell.g,
                    "b": cell.b,
                    "r_raw": cell.r_raw,
                    "g_raw": cell.g_raw,
                    "b_raw": cell.b_raw,
                }
                for cell in tpcb.cells
            ],
        })
    return json.dumps(payload, separators=(",", ":"))


def shan_from_json(text: str, original_bytes: bytes = b"") -> Shan:
    payload = json.loads(text)
    tpcbs = []
    for item in payload.get("tpcbs", []):
        cells = [
            ShCell(
                r=list(cell["r"]),
                g=list(cell["g"]),
                b=list(cell["b"]),
                r_raw=list(cell.get("r_raw", [0, 0, 0, 0])),
                g_raw=list(cell.get("g_raw", [0, 0, 0, 0])),
                b_raw=list(cell.get("b_raw", [0, 0, 0, 0])),
            )
            for cell in item.get("cells", [])
        ]
        unk_values = item.get("grid_unk_values")
        tpcbs.append(Tpcb(
            file_offset=int(item.get("file_offset", 0)),
            coeff_file_offset=int(item.get("coeff_file_offset", 0)),
            unk1_1=int(item.get("unk1_1", 1)),
            unk1_2=int(item.get("unk1_2", 0)),
            grid_cell_count_xyz=tuple(item["grid_cell_count_xyz"]),
            grid_spacing_xyz=tuple(item["grid_spacing_xyz"]),
            grid_dimensions_xyz=tuple(item["grid_dimensions_xyz"]),
            grid_range_min_xyz=tuple(item["grid_range_min_xyz"]),
            grid_range_max_xyz=tuple(item["grid_range_max_xyz"]),
            unk4=int(item.get("unk4", 12)),
            unk5=float(item.get("unk5", 0.0)),
            unk6=float(item.get("unk6", 1.0)),
            grid_cell_count=int(item["grid_cell_count"]),
            grid_indices=item.get("grid_indices"),
            cells=cells,
            grid_unk_values=[tuple(v) for v in unk_values] if unk_values else None,
        ))
    return Shan(
        unk1=int(payload.get("unk1", 0)),
        unk3=int(payload.get("unk3", 0)),
        name=str(payload.get("name", "")),
        starting_frames=list(payload.get("starting_frames", [])),
        tpcbs=tpcbs,
        original_bytes=original_bytes,
    )
