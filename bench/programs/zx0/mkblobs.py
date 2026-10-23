#!/usr/bin/env python3
"""Make the compressed test blobs for the zx0 and lz4 benchmarks.

Part of the uc80 benchmark harness.
SPDX-License-Identifier: GPL-3.0-or-later

    python3 bench/programs/zx0/mkblobs.py --zx0 path/to/zx0 [--lz4 lz4]

The sample is 8000 bytes made here from fixed seeds: pseudo-English text,
a tile map, a sine table, 8x8 sprite bitmaps and a short run of noise, the
kind of data a Z80 program unpacks.  The script compresses the sample with
the ZX0 compressor (built from github.com/einar-saukas/ZX0, src/zx0.c,
default v2 format) and with the lz4 command (one LZ4 block taken from the
frame), and writes zx0/zx0blob.h and lz4/lz4blob.h.  It prints the
CRC-16/CCITT-FALSE of the sample, which both programs must print.
"""

import argparse
import math
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZE = 8000

WORDS = ("the a of and to in is it that was for on are with as his they at be "
         "this from have or by one had not but what all were when we there can "
         "an your which their said if do will each about how up out them then "
         "she many some so these would other into has more her two like him see "
         "time could no make than first been its who now people my made over did "
         "down only way find use may water long little very after words called "
         "just where most know get through back much before go good new write "
         "our used me man too any day same right look think also around another "
         "came come work three word must because does part even place well such "
         "here take why things help put years different away again off went old "
         "number sound sprite player score level enemy music channel note").split()


class Lcg:
    def __init__(self, seed):
        self.s = seed

    def next(self):
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s >> 16


def sample() -> bytes:
    r = Lcg(1)
    out = bytearray()
    # Text: sentences of random words, 72-column lines.
    line = ""
    while len(out) < 4600:
        n = 4 + r.next() % 9
        words = [WORDS[r.next() % len(WORDS)] for _ in range(n)]
        sent = " ".join(words).capitalize() + ". "
        if len(line) + len(sent) > 72:
            out += (line.rstrip() + "\r\n").encode()
            line = ""
        line += sent
    out = out[:4600]
    # Tile map, 32x24: floor, walls, a few objects.
    for y in range(24):
        for x in range(32):
            if y in (0, 23) or x in (0, 31):
                t = 1
            elif (x % 8 == 4 and y % 6 == 3):
                t = 2 + (x + y) % 3
            elif r.next() % 23 == 0:
                t = 5 + r.next() % 4
            else:
                t = 0
            out.append(t)
    # Sine table, 256 entries, signed 8-bit.
    for i in range(256):
        out.append(int(round(127 * math.sin(2 * math.pi * i / 256))) & 0xFF)
    # Sprites: 64 8x8 1-bpp bitmaps built from a few shapes.
    shapes = [[0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x18],
              [0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81],
              [0xFF, 0x81, 0x81, 0x81, 0x81, 0x81, 0x81, 0xFF],
              [0x10, 0x38, 0x7C, 0xFE, 0x38, 0x38, 0x38, 0x38]]
    for i in range(64):
        s = shapes[i % 4]
        shift = (i // 4) % 8
        for row in s:
            out.append(((row << shift) | (row >> (8 - shift))) & 0xFF)
    # Noise to the end.
    while len(out) < SIZE:
        out.append(r.next() & 0xFF)
    return bytes(out[:SIZE])


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def lz4_block(frame: bytes) -> bytes:
    """Return the one data block of an LZ4 frame (no block checksums)."""
    assert frame[:4] == b"\x04\x22\x4d\x18", "not an LZ4 frame"
    flg = frame[4]
    pos = 6 + (8 if flg & 0x08 else 0) + (4 if flg & 0x01 else 0) + 1
    size = int.from_bytes(frame[pos:pos + 4], "little")
    assert not size & 0x80000000, "block is stored, not compressed"
    pos += 4
    block = frame[pos:pos + size]
    assert int.from_bytes(frame[pos + size:pos + size + 4], "little") == 0, \
        "more than one block"
    return block


def c_array(name: str, data: bytes) -> str:
    rows = [", ".join(f"0x{b:02x}" for b in data[i:i + 12])
            for i in range(0, len(data), 12)]
    return (f"static const unsigned char {name}[{len(data)}] = {{\n    "
            + ",\n    ".join(rows) + "\n};\n")


def header(tag: str, what: str, data: bytes, crc: int) -> str:
    return (f"/* {tag}blob.h - {what} of the 8000-byte sample, made by\n"
            f" * bench/programs/zx0/mkblobs.py.  Do not edit.\n"
            f" * SPDX-License-Identifier: GPL-3.0-or-later\n"
            f" * Sample CRC-16/CCITT-FALSE: {crc:04X} ({crc}). */\n"
            f"#define {tag.upper()}_OUT_SIZE {SIZE}\n"
            + c_array(f"{tag}_blob", data))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zx0", required=True, help="ZX0 compressor binary")
    ap.add_argument("--lz4", default="lz4", help="lz4 command")
    args = ap.parse_args()
    data = sample()
    crc = crc16(data)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "sample.bin"
        raw.write_bytes(data)
        subprocess.run([args.zx0, "-f", str(raw), str(raw) + ".zx0"],
                       check=True, capture_output=True)
        zx0 = Path(str(raw) + ".zx0").read_bytes()
        subprocess.run([args.lz4, "-q", "-f", "-12", "-B4", "-BI",
                        "--no-frame-crc", str(raw), str(raw) + ".lz4"],
                       check=True)
        lz4 = lz4_block(Path(str(raw) + ".lz4").read_bytes())
    (HERE / "zx0blob.h").write_text(header("zx0", "ZX0 v2 stream", zx0, crc))
    (HERE.parent / "lz4" / "lz4blob.h").write_text(
        header("lz4", "LZ4 block", lz4, crc))
    print(f"sample {SIZE} bytes, CRC {crc:04X} ({crc}); zx0 {len(zx0)} bytes, "
          f"lz4 {len(lz4)} bytes")


if __name__ == "__main__":
    main()
