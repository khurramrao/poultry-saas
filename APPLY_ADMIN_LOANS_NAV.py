"""RayNoor UI-only change: move Admin Loans link to Accounting page.

Run from the Django project root, after extracting this file next to manage.py:
    python APPLY_ADMIN_LOANS_NAV.py

No model, view, database, or accounting changes are made.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SIDEBAR = ROOT / 'api/templates/api/includes/egg_pos_sidebar_nav.html'
ACCOUNTING = ROOT / 'api/templates/api/egg_pos_accounting.html'
LOAN_URL = "{% url 'egg_pos_admin_loans' %}"


def prepare_sidebar(source: str) -> str:
    if LOAN_URL not in source:
        raise RuntimeError('Expected Admin Loans sidebar link was not found. No files changed.')

    lines = source.splitlines(keepends=True)
    matching = [i for i, line in enumerate(lines) if LOAN_URL in line]
    if len(matching) != 1:
        raise RuntimeError('Ambiguous Admin Loans sidebar link. No files changed.')
    index = matching[0]
    line = lines[index]
    if line.count('<a ') != 1 or 'Admin Loans' not in line:
        raise RuntimeError('Admin Loans navigation is not on its own line. No files changed.')
    if not ('{% if request.user.is_superuser or request.user.is_staff %}' in line
            and '{% endif %}' in line):
        raise RuntimeError('Unexpected Admin Loans sidebar permissions. No files changed.')
    del lines[index]
    changed = ''.join(lines)

    # Keep the parent Accounts & Transfers nav item selected on Admin Loans screen.
    marker = "or page == 'egg_pos_transfer_funds'"
    if changed.count(marker) != 1:
        raise RuntimeError('Could not locate Accounts & Transfers active-link rule. No files changed.')
    if "or page == 'egg_pos_admin_loans'" not in changed:
        changed = changed.replace(marker, marker + " or page == 'egg_pos_admin_loans'", 1)
    return changed


def prepare_accounting(source: str) -> str:
    if LOAN_URL in source:
        # A previously installed link exists; avoid duplicated buttons.
        return source
    pattern = r'(<a\s+[^>]*href="\{% url [\'\"]egg_pos_transfer_funds[\'\"] %\}"[^>]*>.*?</a>)'
    matches = list(re.finditer(pattern, source, flags=re.DOTALL))
    if len(matches) != 1:
        raise RuntimeError('Could not locate unique Transfer Funds button. No files changed.')
    new_button = (
        '{% if request.user.is_superuser or request.user.is_staff %}'
        '<a class="ep-btn" href="{% url \'egg_pos_admin_loans\' %}">'
        '<i class="fa-solid fa-hand-holding-dollar"></i> Admin Loans</a>'
        '{% endif %}'
    )
    m = matches[0]
    return source[:m.end()] + new_button + source[m.end():]


def main() -> None:
    if not (ROOT / 'manage.py').is_file():
        raise RuntimeError('Place this script next to manage.py in the RayNoor Django project.')
    for path in (SIDEBAR, ACCOUNTING):
        if not path.is_file():
            raise RuntimeError(f'Required template is missing: {path.relative_to(ROOT)}')
    before_sidebar = SIDEBAR.read_text(encoding='utf-8')
    before_accounting = ACCOUNTING.read_text(encoding='utf-8')

    # Validate both transformations before writing anything.
    if LOAN_URL in before_sidebar:
        after_sidebar = prepare_sidebar(before_sidebar)
    elif "or page == 'egg_pos_admin_loans'" in before_sidebar and LOAN_URL in before_accounting:
        after_sidebar = before_sidebar  # Already applied.
    else:
        raise RuntimeError('Unexpected sidebar structure. No files changed.')
    after_accounting = prepare_accounting(before_accounting)
    if LOAN_URL not in after_accounting:
        raise RuntimeError('Admin Loans button was not inserted. No files changed.')

    changes = [(SIDEBAR, before_sidebar, after_sidebar), (ACCOUNTING, before_accounting, after_accounting)]
    changes = [(p, old, new) for p, old, new in changes if new != old]
    if not changes:
        print('Already installed. No files changed.')
        return

    backups = ROOT / ('raynoor_admin_loan_nav_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    for path, old, _ in changes:
        backup = backups / path.relative_to(ROOT)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(old, encoding='utf-8')
    for path, _, new in changes:
        path.write_text(new, encoding='utf-8')
        print('Updated:', path.relative_to(ROOT))
    print('Backups:', backups.name)
    print('No database migration needed. Run: python manage.py check')


if __name__ == '__main__':
    main()
