"""Safe, targeted RayNoor pre-booking UI/report + POS draft bridge.

Run beside manage.py after extracting the WHOLE ZIP. No migrations or DB writes.
Updates only two existing prebooking files plus narrow insertions into the
existing POS sale view/template. STOPs before changing anything if conflicts.
"""
from datetime import datetime
from pathlib import Path
import ast
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / 'upgrade_payload'
PATCHES = ROOT / 'patches'
TAG = 'PREBOOK_UPGRADE_20261103'

# SHA256 fingerprints from the previously supplied Manual Pre-Bookings ZIP.
ORIGINAL_HASHES = {
    'api/views/egg_pos_prebookings.py': '9e70ed9def0aab1c3bbbb9a5e7ad34ab420418b4895591e163e6591e0e55fec0',
    'api/templates/api/egg_pos_manual_prebookings.html': 'b0117862d53726c3a917f5c8188a5bb9eecc42dca551af79e3e89bf1e6994e94',
}
NEW_FILES = [
    'api/services/egg_pos_prebooking_sale.py',
    'api/prebooking_upgrade_tests.py',
]
EXISTING = list(ORIGINAL_HASHES) + [
    'api/views/egg_pos.py',
    'api/templates/api/egg_pos_sale_new.html',
]


def fail(message):
    raise SystemExit('STOP (no changes made): ' + message)


def sha(value):
    if isinstance(value, str): value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def apply_hunks(text, patch_file):
    patches = json.loads((PATCHES / patch_file).read_text(encoding='utf-8'))
    # Reverse order prevents near-adjacent patch contexts from overlapping.
    for p in reversed(patches):
        occurrences = text.count(p['target'])
        if occurrences != 1:
            fail(f"Could not safely locate POS patch anchor (around source line {p['base_at_line']}) "
                 f"in {patch_file}. Found {occurrences}; current code differs from the older snapshot. "
                 'No files changed. Please share current api/views/egg_pos.py or sale template for exact merge.')
        text = text.replace(p['target'], p['replacement'], 1)
    return text


def install():
    if not (ROOT / 'manage.py').is_file():
        fail('Run from the poultry project root beside manage.py.')
    if not (ROOT / 'api/migrations/0086_egg_pos_manual_prebookings.py').is_file():
        fail('The original Manual Pre-Bookings feature (migration 0086) is not installed.')
    for name in EXISTING:
        if not (ROOT / name).is_file(): fail('Project file missing: ' + name)
    for name in list(ORIGINAL_HASHES) + NEW_FILES:
        if not (PACKAGE / name).is_file(): fail('ZIP incomplete: ' + name)
    for name in ('sale_py.json', 'sale_html.json'):
        if not (PATCHES / name).is_file(): fail('ZIP incomplete: patches/' + name)

    proposed = {}
    original = {name: (ROOT/name).read_text(encoding='utf-8') for name in EXISTING}
    for name in ORIGINAL_HASHES:
        current = original[name]
        wanted = (PACKAGE / name).read_text(encoding='utf-8')
        if sha(current) not in (ORIGINAL_HASHES[name], sha(wanted)):
            fail(name + ' differs from the earlier Manual Pre-Bookings release. ' 
                 'To avoid overwriting your edits, no files were changed. Please share this file for an exact merge.')
        proposed[name] = wanted

    sale_view = 'api/views/egg_pos.py'
    sale_html = 'api/templates/api/egg_pos_sale_new.html'
    view_is_new = TAG in original[sale_view]
    html_is_new = TAG in original[sale_html]
    if view_is_new != html_is_new:
        fail('Only one of the POS sale files has the pre-booking bridge. Stop to avoid a partial install.')
    if view_is_new:
        proposed[sale_view] = original[sale_view]
        proposed[sale_html] = original[sale_html]
    else:
        proposed[sale_view] = apply_hunks(original[sale_view], 'sale_py.json')
        proposed[sale_html] = apply_hunks(original[sale_html], 'sale_html.json')
    if TAG not in proposed[sale_view] or TAG not in proposed[sale_html]:
        fail('POS patch marker validation failed.')
    for name in (sale_view, 'api/views/egg_pos_prebookings.py'):
        try:
            ast.parse(proposed[name], filename=name)
        except SyntaxError as exc:
            fail(f'Python check failed for {name}: {exc}')
    for name in NEW_FILES:
        existing = ROOT/name
        source = PACKAGE/name
        if existing.is_file() and existing.read_bytes() != source.read_bytes():
            fail('New module already exists with different contents: '+name)

    changes = [(name, result) for name, result in proposed.items() if result != original[name]]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backups = []
    copied = []
    try:
        for name, data in changes:
            dest = ROOT/name
            backup = dest.with_name(dest.name + f'.prebookupgrade_{timestamp}.bak')
            shutil.copy2(dest, backup)
            backups.append((dest, backup))
            dest.write_text(data, encoding='utf-8')
            print('Updated:',name,'(backup:',backup.name+')')
        for name in NEW_FILES:
            src = PACKAGE/name
            dst = ROOT/name
            if not dst.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(dst)
                print('Added:',name)
            else:
                print('Already installed:', name)
    except Exception as exc:
        for dst, backup in backups:
            shutil.copy2(backup,dst)
        for dst in copied:
            dst.unlink(missing_ok=True)
        fail('Write failed and backups restored: ' + str(exc))

    print('\nSUCCESS. Compact pre-booking entry, verified platform analytics and prefilled POS draft are installed.')
    print('No migration needed for this update. No database operations performed by installer.')
    print('Run: python manage.py check')
    print('Run: python manage.py makemigrations --check --dry-run')
    print('Run: python manage.py test api.manual_prebooking_tests api.prebooking_upgrade_tests')
    print('After testing, verify flow from confirmed booking to prefilled POS checkout.')


if __name__ == '__main__':
    install()
