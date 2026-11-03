"""Targeted and repeat-safe RayNoor social pre-bookings installer (no database operations).

Run only from project root after extracting ZIP alongside manage.py:
    python INSTALL_MANUAL_PREBOOKINGS.py

Changes only two small registrations and optional navigation; places new feature files.
No existing Egg POS accounting, inventory, sales, payroll or online order modules replaced.
"""
from pathlib import Path
from datetime import datetime
import ast
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / 'prebook_payload'
NEW = [
    'api/models/egg_pos_prebooking.py',
    'api/views/egg_pos_prebookings.py',
    'api/templates/api/egg_pos_manual_prebookings.html',
    'api/migrations/0086_egg_pos_manual_prebookings.py',
    'api/manual_prebooking_tests.py',
]
REGISTRATIONS = ['api/models/__init__.py', 'api/urls.py']
NAV_OPTIONS = ['api/templates/api/includes/egg_pos_sidebar_nav.html',
               'api/templates/api/egg_pos_base.html']


def stop(message):
    raise SystemExit('STOP (nothing changed): ' + message)


def insert_nav(content, name):
    if "'egg_pos_manual_prebookings'" in content or '"egg_pos_manual_prebookings"' in content:
        return content
    # Match the first existing Inventory link; include and base layouts differ.
    anchor = re.search(r'(?m)^[ \t]*<a\b[^\n]*href=["\']\{%\s*url\s*["\']egg_pos_inventory["\']\s*%\}["\'][^\n]*', content)
    if not anchor:
        return content
    indent = re.match(r'[ \t]*', anchor.group(0)).group(0)
    link = (indent + '{% if request.user.is_superuser or request.user.is_staff %}'
            '<a href="{% url \'egg_pos_manual_prebookings\' %}" '
            'class="{% if request.resolver_match.url_name == \'egg_pos_manual_prebookings\' %}active{% endif %}">'
            '<i class="fa-solid fa-clipboard-list"></i><span>Manual Pre-Bookings</span></a>{% endif %}\n')
    return content[:anchor.start()] + link + content[anchor.start():]


def install():
    if not (ROOT / 'manage.py').is_file():
        stop('Extract the full ZIP into your poultry project root beside manage.py.')
    required = REGISTRATIONS
    for name in required:
        if not (ROOT / name).is_file():
            stop(f'Project file missing: {name}')
    # Avoid migration-number collisions or accidental replacement of unrelated 0086.
    for candidate in sorted((ROOT / 'api/migrations').glob('0086*.py')):
        if candidate.name != '0086_egg_pos_manual_prebookings.py':
            stop(f'Another 0086 migration exists ({candidate.name}). Do not overwrite it; share the file name for safe dependency adjustment.')
    for name in NEW:
        src = PAYLOAD / name
        dest = ROOT / name
        if not src.is_file():
            stop(f'Incomplete ZIP: missing {src.relative_to(ROOT)}. Extract the whole ZIP, not only the installer.')
        if dest.is_file() and dest.read_bytes() != src.read_bytes():
            stop(f'{name} already exists and differs. Nothing overwritten; please request a merge.')

    versions = {name: (ROOT / name).read_text(encoding='utf-8') for name in REGISTRATIONS}
    proposed = dict(versions)
    name = REGISTRATIONS[0]
    if 'from .egg_pos_prebooking import' not in proposed[name]:
        proposed[name] += ('\n# Standalone social-media pre-bookings; not connected to online orders or POS transactions.\n'
                           'from .egg_pos_prebooking import (EggPOSManualPrebooking, EggPOSManualPrebookingLine, '
                           'EggPOSManualPrebookingEvent)\n')
    name = REGISTRATIONS[1]
    s = proposed[name]
    if not re.search(r'(?m)^\s*from\s+api\.views\s+import\s+egg_pos_prebookings\s*$', s):
        m = re.search(r'(?m)^urlpatterns\s*=\s*\[', s)
        if not m:
            stop('Cannot locate urlpatterns in api/urls.py')
        s = s[:m.start()] + 'from api.views import egg_pos_prebookings\n' + s[m.start():]
    occurrences = len(re.findall(r'name\s*=\s*["\']egg_pos_manual_prebookings["\']', s))
    if occurrences > 1:
        stop('URL already has multiple egg_pos_manual_prebookings names. No changes made.')
    if not occurrences:
        m = re.search(r'(?m)^urlpatterns\s*=\s*\[', s)
        if not m:
            stop('Cannot safely add URL to api/urls.py')
        s = (s[:m.end()] + '\n    path("egg-pos/manual-prebookings/", '
             'egg_pos_prebookings.manual_prebookings, name="egg_pos_manual_prebookings"),\n' + s[m.end():])
    proposed[name] = s
    for filename in REGISTRATIONS:
        try:
            ast.parse(proposed[filename], filename=filename)
        except SyntaxError as exc:
            stop(f'Python syntax check failed in {filename}: {exc}')

    nav_count = 0
    for name in NAV_OPTIONS:
        if (ROOT / name).is_file():
            old = (ROOT / name).read_text(encoding='utf-8')
            changed = insert_nav(old, name)
            if changed != old:
                proposed[name] = changed
                versions[name] = old
                nav_count += 1
    # Apply changes only after all conflicts and syntax checks have passed.
    copies = []
    changes = [(n, proposed[n]) for n in proposed if proposed[n] != versions[n]]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        for name in NEW:
            src, dest = PAYLOAD / name, ROOT / name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                copies.append(dest)
                print('Added:', name)
            else:
                print('Already installed:', name)
        for name, output in changes:
            path = ROOT / name
            backup = path.with_name(path.name + '.prebook_' + timestamp + '.bak')
            shutil.copyfile(path, backup)
            path.write_text(output, encoding='utf-8')
            print('Updated:', name, '(backup:', backup.name + ')')
    except Exception as exc:
        # Restore any in-place edits if file writing itself fails (not needed for expected validations).
        for name, old in versions.items():
            if name in dict(changes):
                (ROOT / name).write_text(old, encoding='utf-8')
        for dest in copies:
            dest.unlink(missing_ok=True)
        stop(f'File installation failed: {exc}')
    print('\nINSTALL COMPLETE. Existing accounting, POS sales, stock and website orders were not modified.')
    if not nav_count:
        print('INFO: Existing sidebar may already contain a link or use a different design.')
    print('Next run: python manage.py check')
    print('Then: python manage.py makemigrations --check --dry-run')
    print('Then: python manage.py migrate (after database backup)')
    print('Then: python manage.py test api.manual_prebooking_tests')
    print('Page: /api/egg-pos/manual-prebookings/')


if __name__ == '__main__':
    install()
