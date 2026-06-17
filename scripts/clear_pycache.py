"""Clear all __pycache__ directories under dashboard/ and scripts/."""
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
removed = 0
for p in root.rglob('__pycache__'):
    if p.is_dir():
        shutil.rmtree(p)
        removed += 1
print(f'  cleared {removed} __pycache__ directories')
