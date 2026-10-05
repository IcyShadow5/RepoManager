"""Capture global Qt warnings as well as QQmlEngine diagnostics in UI probes."""
import os
from pathlib import Path
import sys
from PySide6.QtCore import QtMsgType, qInstallMessageHandler


class QtMessages:
    def __init__(self):
        # The offscreen platform uses a font directory instead of Windows'
        # native font database. Give the synthetic platform real system fonts.
        if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            if fonts.is_dir():
                os.environ.setdefault("QT_QPA_FONTDIR", str(fonts))
        self.warnings = []
        self.previous = qInstallMessageHandler(self.receive)

    def receive(self, kind, context, message):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            self.warnings.append(message)
            print("QT: " + message, file=sys.stderr)
        if self.previous is not None:
            self.previous(kind, context, message)

    def restore(self):
        qInstallMessageHandler(self.previous)
