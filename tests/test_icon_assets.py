"""The application and packaging resolve the same complete Windows identity."""
from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET


class IconAssetTests(unittest.TestCase):
    def test_both_windows_icons_have_all_sizes_and_alpha_png_frames(self):
        root = Path(__file__).resolve().parents[1]
        canonical = (root / "appicon.ico").read_bytes()
        self.assertEqual(canonical, (root / "app.ico").read_bytes())
        reserved, kind, count = struct.unpack_from("<HHH", canonical)
        self.assertEqual((reserved, kind, count), (0, 1, 7))
        sizes = []
        for index in range(count):
            width, height, _, _, planes, depth, length, offset = struct.unpack_from("<BBBBHHII", canonical, 6 + 16 * index)
            self.assertEqual(width, height)
            self.assertEqual((planes, depth), (1, 32))
            frame = canonical[offset:offset + length]
            self.assertTrue(frame.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(len(frame), length)
            self.assertEqual(frame[25], 6, "PNG frame must retain RGBA")
            sizes.append(width or 256)
        self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256])

    def test_vector_source_is_valid_and_uses_shared_palette(self):
        root = Path(__file__).resolve().parents[1]
        asset = root / "assets/repomanager.svg"
        svg = ET.parse(asset).getroot()
        self.assertEqual(svg.attrib["viewBox"], "0 0 64 64")
        self.assertIn("#32caff", asset.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
