"""Install only a Django authentication backend and an optional login-page hint.

Run from the RayNoor project root (the folder containing manage.py).
No model/migration changes. Safe to run twice. Does not modify account data.
"""
from __future__ import annotations
import ast
from datetime import datetime
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'api' / 'auth_backends.py'
TEST_SOURCE = ROOT / 'api' / 'case_insensitive_login_tests.py'
SETTINGS = ROOT / 'farm_project' / 'settings.py'
LOGIN_TEMPLATE = ROOT / 'api' / 'templates' / 'api' / 'login.html'
BACKEND_IMPORT = 'api.auth_backends.UniqueCaseInsensitiveUsernameBackend'
MARK = '# RAYNOOR_CASE_INSENSITIVE_LOGIN'


def die(message):
    raise SystemExit('INSTALL STOPPED (no changes made): ' + message)


def main():
    if not (ROOT / 'manage.py').is_file():
        die('Run this installer beside manage.py.')
    if not SOURCE.is_file() or not TEST_SOURCE.is_file() or not SETTINGS.is_file():
        die('Extract the full ZIP with its api/ folder next to this installer.')

    settings_text = SETTINGS.read_text(encoding='utf-8')
    # Don't override an existing custom authentication backend configuration.
    try:
        tree = ast.parse(settings_text)
    except SyntaxError:
        die('settings.py is not valid Python.')
    assigned = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(x, ast.Name) and x.id == 'AUTHENTICATION_BACKENDS' for x in node.targets):
                assigned.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'AUTHENTICATION_BACKENDS':
            assigned.append(node)
    if assigned and BACKEND_IMPORT not in settings_text:
        die('settings.py defines custom AUTHENTICATION_BACKENDS. Merge manually to preserve them.')

    # If a backend file already exists, don't overwrite unrelated local changes.
    dst = ROOT / 'api' / 'auth_backends.py'
    if dst.exists() and dst.read_bytes() != SOURCE.read_bytes():
        existing = dst.read_text(encoding='utf-8')
        if 'class UniqueCaseInsensitiveUsernameBackend(' not in existing:
            die('api/auth_backends.py exists with different content; merge manually.')
        backend_action = 'Existing compatible backend retained'
    else:
        backend_action = 'Backend ready'

    changes = []
    if not assigned:
        settings_text = settings_text.rstrip() + (
            '\n\n' + MARK + '\n'
            "AUTHENTICATION_BACKENDS = [\n"
            "    'api.auth_backends.UniqueCaseInsensitiveUsernameBackend',\n"
            "]\n"
        )
        changes.append((SETTINGS, settings_text.encode('utf-8')))
    if not dst.exists():
        changes.append((dst, SOURCE.read_bytes()))

    # Display hint only when the template has the expected existing label.
    if LOGIN_TEMPLATE.is_file():
        old_template = LOGIN_TEMPLATE.read_text(encoding='utf-8')
        old = '<label>Username</label>'
        new = '<label>Username</label>\n        <small style="display:block;color:#64748b;margin-bottom:6px">Capital letters are optional.</small>'
        if old in old_template and new not in old_template:
            changes.append((LOGIN_TEMPLATE, old_template.replace(old, new, 1).encode('utf-8')))

    # Keep test files out of scope if already modified locally.
    test_dest = ROOT / 'api' / 'case_insensitive_login_tests.py'
    if not test_dest.exists():
        changes.append((test_dest, TEST_SOURCE.read_bytes()))

    if not changes:
        print('Already installed. No files changed. ' + backend_action)
        return

    backup = ROOT / 'raynoor_login_backups' / datetime.now().strftime('%Y%m%d_%H%M%S')
    for dest, content in changes:
        if dest.exists():
            bk = backup / dest.relative_to(ROOT)
            bk.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, bk)
    for dest, content in changes:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        print('Updated:', dest.relative_to(ROOT))
    print('Backups:', backup.relative_to(ROOT))
    print('Next: python manage.py check')
    print('Then: python manage.py test api.case_insensitive_login_tests')
    print('No database migration is required.')


if __name__ == '__main__':
    main()
