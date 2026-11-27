from pathlib import Path
import shutil
import sys
from datetime import datetime

PATCH_DIR = Path(__file__).resolve().parent / 'patch_files'
PROJECT_ROOT = Path.cwd()

def main():
    manage_py = PROJECT_ROOT / 'manage.py'
    api_dir = PROJECT_ROOT / 'api'
    if not manage_py.exists() or not api_dir.exists():
        print('STOPPED: Run this from your Django project root (the folder containing manage.py and api).')
        sys.exit(1)
    if not PATCH_DIR.exists():
        print('STOPPED: patch_files folder not found next to this installer.')
        sys.exit(1)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_root = PROJECT_ROOT / 'raynoor_shop_product_polish_backups' / stamp
    backup_root.mkdir(parents=True, exist_ok=True)

    changed = []
    for src in PATCH_DIR.rglob('*'):
        if not src.is_file():
            continue
        rel = src.relative_to(PATCH_DIR)
        dest = PROJECT_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            backup_path = backup_root / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup_path)
        shutil.copy2(src, dest)
        changed.append(str(rel).replace('/', '\\'))
        print(f'Updated: {str(rel).replace("/", "\\")}')

    print(f'Backups: {backup_root}')
    print('Next: python manage.py check')
    print('If all good, deploy/restart Railway.')

if __name__ == '__main__':
    main()
