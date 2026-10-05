"""RepoManager Development desktop entry point (PySide6/QML)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

def main():
    try:
        from run_qt import main as start
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("PySide6"):
            raise
        message = ("RepoManager Development requires PySide6.\n\n"
                   "Install the development dependencies in a local environment:\n"
                   ".venv\\Scripts\\python.exe -m pip install -r packaging\\requirements-qt.txt")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "RepoManager — Qt runtime required", 0x10)
        else:
            print(message, file=sys.stderr)
        return 2
    return start()

if __name__ == "__main__":
    raise SystemExit(main())
