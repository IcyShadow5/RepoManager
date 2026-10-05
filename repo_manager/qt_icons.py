"""Render the bundled Tabler SVGs with the QML palette, including software QA."""
import re
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer


class IconProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.directory = Path(__file__).parent / "qml" / "assets" / "tabler"

    def requestImage(self, identifier, size, requested_size):
        name, _, color = identifier.partition("/")
        if not re.fullmatch(r"[a-z0-9-]+", name):
            return QImage()
        path = self.directory / f"{name}.svg"
        if not path.is_file():
            return QImage()
        tint = f"#{color}" if re.fullmatch(r"[0-9a-fA-F]{6}", color) else "#35caff"
        width = requested_size.width() if requested_size.width() > 0 else 24
        height = requested_size.height() if requested_size.height() > 0 else 24
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("transparent"))
        svg = path.read_bytes().replace(b"currentColor", tint.encode("ascii"))
        painter = QPainter(image)
        QSvgRenderer(svg).render(painter)
        painter.end()
        size.setWidth(width)
        size.setHeight(height)
        return image
