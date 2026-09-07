"""Entry point for RepoManager — safe for direct execution (pythonw run.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_manager.main import main

if __name__ == "__main__":
    main()
