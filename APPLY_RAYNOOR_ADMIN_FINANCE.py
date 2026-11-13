"""Targeted, backup-first integration of the RayNoor admin finance patch.

Run from the Django project root *after* extracting the patch into that root:
    python APPLY_RAYNOOR_ADMIN_FINANCE.py
This script does not touch production data or execute migrations.
"""
from pathlib import Path
from datetime import datetime
import sys

ROOT = Path(__file__).resolve().parent
BACKUPS = ROOT / ('raynoor_admin_finance_backups_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
STAGED = {}


def patch(rel, marker, transform):
    path = ROOT / rel
    if not path.is_file():
        raise RuntimeError(f'Missing required project file: {rel}. Use your latest RayNoor source.')
    original = STAGED[path][0] if path in STAGED else path.read_text(encoding='utf-8')
    s = STAGED[path][1] if path in STAGED else original
    if marker in s:
        print(f'Already installed: {rel} ({marker})')
        return
    changed = transform(s)
    if changed == s or marker not in changed:
        raise RuntimeError(f'Could not safely update {rel}; the existing version differs. No files were changed.')
    STAGED[path] = (original, changed)
    print(f'Prepared: {rel}')


def replace_exact(s, old, new):
    if s.count(old) != 1:
        raise RuntimeError(f'Expected one integration point, found {s.count(old)}: {old[:80]}')
    return s.replace(old, new, 1)


def after_line(s, token, addition):
    lines = s.splitlines(keepends=True)
    indices = [i for i, v in enumerate(lines) if token in v]
    if len(indices) != 1:
        raise RuntimeError(f'Cannot find unique integration line: {token}')
    i = indices[0]
    lines.insert(i + 1, addition)
    return ''.join(lines)


def append(s, t):
    return s.rstrip() + '\n' + t + '\n'


def main():
    if not (ROOT / 'manage.py').exists():
        raise RuntimeError('Run this script from your RayNoor Django project folder, next to manage.py.')
    if not (ROOT / 'api/migrations/0095_prebooking_jar_units.py').exists():
        raise RuntimeError('Missing migration 0095_prebooking_jar_units. Install the previous ghee/jar prebooking patch before this update.')
    conflicts = [p.name for p in (ROOT / 'api/migrations').glob('0096_*.py') if p.name != '0096_admin_loan_receivable.py']
    if conflicts:
        raise RuntimeError(f'Another migration 0096 already exists: {conflicts}. Merge migrations before continuing.')
    for rel in [
        'api/models/egg_pos_admin_loan.py',
        'api/services/egg_pos_admin_loans.py',
        'api/views/egg_pos_admin_loans.py',
        'api/views/egg_pos_admin_expense_correction.py',
        'api/templates/api/egg_pos_admin_loans.html',
        'api/migrations/0096_admin_loan_receivable.py',
        'api/admin_finance_update_tests.py',
    ]:
        if not (ROOT / rel).exists():
            raise RuntimeError(f'Missing new patch file {rel}; extract the ZIP into this project root first.')

    patch('api/models/__init__.py', 'from .egg_pos_admin_loan import EggPOSAdminLoanTransaction',
          lambda s: append(s, 'from .egg_pos_admin_loan import EggPOSAdminLoanTransaction'))
    patch('api/services/accounting.py', '"1320": ("Loans Receivable from Administrators", "asset", "debit")',
          lambda s: after_line(s, '"1310":', '    "1320": ("Loans Receivable from Administrators", "asset", "debit"),\n'))
    patch('api/services/egg_pos_admin_expenses.py', 'expense.void_reason.startswith("[CORRECTION ")',
          lambda s: replace_exact(s, 'entry_date=timezone.localdate(expense.voided_at),',
                    'entry_date=(expense.expense_date if expense.void_reason.startswith("[CORRECTION ") else timezone.localdate(expense.voided_at)),'))
    patch('api/services/egg_pos_accounting.py', 'from api.services.egg_pos_admin_loans import rebuild_admin_loans',
          lambda s: replace_exact(s, '    from api.services.egg_pos_admin_expenses import rebuild_admin_expenses',
                    '    from api.services.egg_pos_admin_loans import rebuild_admin_loans\n    rebuild_admin_loans()\n    from api.services.egg_pos_admin_expenses import rebuild_admin_expenses'))
    patch('api/urls.py', 'from api.views import egg_pos_admin_loans, egg_pos_admin_expense_correction',
          lambda s: after_line(s, 'from api.views import egg_pos_admin_expenses',
                    'from api.views import egg_pos_admin_loans, egg_pos_admin_expense_correction\n'))
    patch('api/urls.py', 'name="egg_pos_admin_loans"',
          lambda s: after_line(s, 'name="egg_pos_admin_expenses"',
                    '    path("egg-pos/admin-loans/", egg_pos_admin_loans.admin_loans, name="egg_pos_admin_loans"),\n'))
    patch('api/urls.py', 'name="egg_pos_correct_admin_expense"',
          lambda s: after_line(s, 'name="egg_pos_void_admin_expense"',
                    '    path("egg-pos/admin-expenses/<int:expense_id>/correct/", egg_pos_admin_expense_correction.correct_admin_expense, name="egg_pos_correct_admin_expense"),\n'))
    sidebar = '<a href="{% url \'egg_pos_admin_loans\' %}" class="{% if page == \'egg_pos_admin_loans\' %}active{% endif %}"><i class="fa-solid fa-hand-holding-dollar"></i><span>Admin Loans</span></a>'
    patch('api/templates/api/includes/egg_pos_sidebar_nav.html', "{% url 'egg_pos_admin_loans' %}",
          lambda s: after_line(s, "<a href=\"{% url 'egg_pos_admin_expenses' %}\"", f'{{% if request.user.is_superuser or request.user.is_staff %}}{sidebar}{{% endif %}}\n'))
    newform = (ROOT / 'patch_snippets/admin_expense_correct_form.html').read_text(encoding='utf-8')
    patch('api/templates/api/egg_pos_admin_expenses.html', "{% url 'egg_pos_correct_admin_expense' e.id %}",
          lambda s: replace_exact(s, '            <details class="ep-adm-correct">', newform + '            <details class="ep-adm-correct">'))
    status_block = (ROOT / 'patch_snippets/payroll_status_view.txt').read_text(encoding='utf-8')
    patch('api/views/egg_pos_payroll.py', 'commission_status = []',
          lambda s: replace_exact(s, '    plans = list(EggPOSSalaryPlan.objects.select_related', status_block + '    plans = list(EggPOSSalaryPlan.objects.select_related')
          .replace("        'staff': staff, 'plans': plans, 'periods': periods, 'advances': advances, 'payments': payments,",
                   "        'staff': staff, 'plans': plans, 'periods': periods, 'advances': advances, 'payments': payments,\n        'commission_status': commission_status, 'cutoff_end': cutoff_end,"))
    html_block = (ROOT / 'patch_snippets/payroll_status_html.txt').read_text(encoding='utf-8')
    patch('api/templates/api/egg_pos_payroll.html', 'Commission close — per employee',
          lambda s: replace_exact(s, '<h3 class="payroll-section-title">1. Set Salary / Change Monthly Rate</h3>',
                    '<h3 class="payroll-section-title">1. Set Salary / Change Monthly Rate</h3>')
                    .replace('<div class="payroll-panel">\n  <h3 class="payroll-section-title">1. Set Salary / Change Monthly Rate</h3>',
                             html_block + '<div class="payroll-panel">\n  <h3 class="payroll-section-title">1. Set Salary / Change Monthly Rate</h3>'))

    # Nothing is modified until all old files are verified and new files exist.
    for path, (original, updated) in STAGED.items():
        backup = BACKUPS / path.relative_to(ROOT)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(original, encoding='utf-8')
        path.write_text(updated, encoding='utf-8')
    print(f'Applied {len(STAGED)} integration files. Backups: {BACKUPS.name}')
    print('Next: python manage.py check && python manage.py makemigrations --check --dry-run && python manage.py migrate')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'INSTALLATION STOPPED: {exc}', file=sys.stderr)
        sys.exit(1)
