"""Where the tensor data starts in a safetensors file we WRITE.

The spec only demands 8-byte alignment. Metal demands 16 for a buffer offset,
and a misaligned data section does not error — every tensor silently falls back
to a copy instead of a zero-copy mmap view. vpipe (tgo-app-dev) measured 39.1 GB
of that on a single checkpoint whose data began at 8 (mod 16).

The arithmetic is the trap. The layout is:

    [8-byte little-endian header length][header blob][tensor data]

so data starts at 8 + len(blob). Padding the BLOB to a multiple of 16 puts the
data at 8 (mod 16) — misaligned every single time, i.e. strictly worse than the
8-byte padding it replaces. The correct condition is len(blob) == 8 (mod 16).
"""

import re
import unittest
from pathlib import Path

PANEL = Path(__file__).with_name("mlx_ltx_panel.py")


def _pad(blob_len: int) -> int:
    """The padding rule as it exists in the panel source, not a copy of it."""
    src = PANEL.read_text()
    m = re.search(r'blob \+= b" " \* \(\(([^\n]+?)\) % (\d+)\)', src)
    assert m, "padding expression not found — did the writer change shape?"
    expr, mod = m.group(1), int(m.group(2))
    return eval(expr.replace("len(blob)", str(blob_len))) % mod   # noqa: S307


class DataSectionAlignment(unittest.TestCase):
    def test_data_starts_16_byte_aligned_for_every_header_length(self):
        for blob_len in range(1, 1024):
            data_start = 8 + blob_len + _pad(blob_len)
            self.assertEqual(data_start % 16, 0,
                             f"header {blob_len} -> data at {data_start} "
                             f"({data_start % 16} mod 16)")

    def test_still_satisfies_the_spec_minimum(self):
        """16-alignment implies the spec's 8 — assert it rather than assume."""
        for blob_len in range(1, 256):
            self.assertEqual((8 + blob_len + _pad(blob_len)) % 8, 0)

    def test_padding_is_minimal(self):
        """Never add a whole extra 16 bytes when zero would do."""
        for blob_len in range(1, 256):
            self.assertLess(_pad(blob_len), 16)

    def test_a_naive_16_pad_would_be_wrong(self):
        """Documents the trap: this is the fix that looks right and is not."""
        for blob_len in (200, 201, 240):
            naive = (-blob_len) % 16
            self.assertNotEqual((8 + blob_len + naive) % 16, 0)


if __name__ == "__main__":
    unittest.main()
