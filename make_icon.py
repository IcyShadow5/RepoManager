"""Export the shared SVG identity to both Windows multi-resolution icons.

Run with the Qt development environment. PNG frames retain alpha at each
Windows icon size; the SVG is the single editable source for the identity.
"""
import os
from pathlib import Path
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(renderer, size):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("Icon frame export failed")
    return bytes(buffer.data())


def main():
    app = QApplication([])
    root = Path(__file__).resolve().parent
    renderer = QSvgRenderer(str(root / "assets/repomanager.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid RepoManager icon source")
    frames = [render(renderer, size) for size in SIZES]
    offset = 6 + 16 * len(frames)
    directory = []
    for size, frame in zip(SIZES, frames):
        directory.append(struct.pack("<BBBBHHII", size % 256, size % 256,
                                     0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    data = struct.pack("<HHH", 0, 1, len(frames)) + b"".join(directory + frames)
    for name in ("appicon.ico", "app.ico"):
        (root / name).write_bytes(data)
    print("Exported appicon.ico and app.ico: " + ", ".join(map(str, SIZES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
