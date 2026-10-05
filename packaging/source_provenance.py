"""Read build identity through the same Git environment boundary as the product."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from repo_manager.git_environment import git_environment


def inspect(root: Path = ROOT) -> dict:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args],
            env=git_environment(read_only=True), timeout=20,
            creationflags=0x08000000 if sys.platform == 'win32' else 0).decode('utf-8').strip()
    return {'head': git('rev-parse', 'HEAD'),
            'dirty': bool(git('status', '--porcelain', '--untracked-files=all'))}


if __name__ == '__main__':
    print(json.dumps(inspect()))
