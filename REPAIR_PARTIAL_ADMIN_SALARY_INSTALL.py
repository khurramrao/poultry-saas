"""RayNoor: finish partial Admin Expenses + Salary & Payroll code integration.

Run from project root after extracting recovery ZIP beside manage.py:
    python REPAIR_PARTIAL_ADMIN_SALARY_INSTALL.py

Does not modify a database, run migrations, overwrite existing feature modules,
or replace whole shared Django files. Validates every planned change before writing.
"""
from __future__ import annotations

import ast
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / 'raynoor_recovery_payload'

ADMIN_NEW = [
    'api/models/egg_pos_admin_expense.py',
    'api/services/egg_pos_admin_expenses.py',
    'api/views/egg_pos_admin_expenses.py',
    'api/templates/api/egg_pos_admin_expenses.html',
    'api/migrations/0084_egg_pos_admin_expenses.py',
    'api/admin_expense_tests.py',
]
SALARY_NEW = [
    'api/models/egg_pos_payroll.py',
    'api/services/egg_pos_payroll.py',
    'api/views/egg_pos_payroll.py',
    'api/templates/api/egg_pos_payroll.html',
    'api/migrations/0085_egg_pos_staff_payroll.py',
    'api/salary_payroll_tests.py',
]
SHARED = [
    'api/models/__init__.py',
    'api/services/accounting.py',
    'api/urls.py',
    'api/templates/api/egg_pos_base.html',
    'api/templates/api/egg_pos_expenses.html',
    'api/services/egg_pos_accounting.py',
    'api/views/egg_pos.py',
]


def stop(message):
    raise SystemExit('STOP (no files changed): ' + message)


def once(text, old, new, desc):
    n = text.count(old)
    if n != 1:
        stop(f'{desc}: expected one anchor, found {n}. Please share your current file for a safe merge.')
    return text.replace(old, new, 1)


def insert_after_first_fuel_link(text, new_link, desc):
    pattern = re.compile(r'(<a\s+[^>]*href=["\']\{%\s*url\s+["\']egg_pos_expenses["\']\s*%\}["\'][^>]*>.*?Fuel\s*&\s*Expenses</a>)', re.S)
    match = pattern.search(text)
    if not match:
        stop(f'{desc}: cannot locate Egg POS sidebar Fuel & Expenses link.')
    return text[:match.end()] + '\n  ' + new_link + text[match.end():]


def do():
    if not (ROOT / 'manage.py').is_file():
        stop('Extract this ZIP into the folder containing manage.py.')
    optional_sidebar = 'api/templates/api/includes/egg_pos_sidebar_nav.html'
    if (ROOT / optional_sidebar).is_file() and optional_sidebar not in SHARED:
        SHARED.append(optional_sidebar)
    for name in SHARED:
        if not (ROOT / name).is_file():
            stop(f'Missing shared file: {name}.')
    for feature, names in [('admin', ADMIN_NEW), ('salary', SALARY_NEW)]:
        for name in names:
            src, target = PAYLOAD / feature / name, ROOT / name
            if not src.is_file():
                stop(f'Recovery ZIP incomplete: {src.relative_to(ROOT)}')
            if target.exists() and target.read_bytes() != src.read_bytes():
                stop(f'{name} already exists but differs from the recovery package. No overwrites allowed.')
    # Prevent applying a previously generated, destructive model-deletion migration.
    for candidate in sorted((ROOT / 'api/migrations').glob('0086*.py')):
        source = candidate.read_text(encoding='utf-8')
        if ('DeleteModel' in source or 'RemoveField' in source) and any(
            s in source for s in ('EggPOSSalary', 'EggPOSPayroll', 'EggPOSAdminExpense')
        ):
            stop(f'Potentially destructive migration found: {candidate.name}. Do not apply it; review it first.')

    current = {name: (ROOT / name).read_text(encoding='utf-8') for name in SHARED}
    proposed = dict(current)

    # Django model registry: the main cause of the proposed destructive 0086.
    name = 'api/models/__init__.py'
    s = proposed[name]
    if 'from .egg_pos_admin_expense import EggPOSAdminExpense' not in s:
        s += '\nfrom .egg_pos_admin_expense import EggPOSAdminExpense\n'
    if 'from .egg_pos_payroll import' not in s:
        s += ('\nfrom .egg_pos_payroll import (\n'
              '    EggPOSSalaryPlan, EggPOSPayrollPeriod, EggPOSSalaryAdvance,\n'
              '    EggPOSSalaryAdvanceAllocation, EggPOSSalaryPayment,\n'
              ')\n')
    proposed[name] = s

    # Chart of Accounts: add only missing definitions; reject conflicting account numbers.
    name = 'api/services/accounting.py'
    s = proposed[name]
    codes = [
        ('1500', 'Office Equipment / Fixed Assets', 'asset', 'debit'),
        ('6300', 'Office / Shop Rent', 'expense', 'debit'),
        ('6310', 'Electricity / Utilities Expense', 'expense', 'debit'),
        ('6320', 'Internet / Phone Expense', 'expense', 'debit'),
        ('6330', 'Stationery / Printing Expense', 'expense', 'debit'),
        ('6340', 'Office Consumables Expense', 'expense', 'debit'),
        ('6350', 'Repairs & Maintenance Expense', 'expense', 'debit'),
        ('6360', 'Marketing / Advertising Expense', 'expense', 'debit'),
        ('6370', 'Professional / Admin Fees', 'expense', 'debit'),
        ('6380', 'Other Administrative Expense', 'expense', 'debit'),
        ('1310', 'Staff Salary Advances', 'asset', 'debit'),
        ('2050', 'Staff Salaries Payable', 'liability', 'credit'),
        ('6390', 'Staff Salaries Expense', 'expense', 'debit'),
    ]
    to_add = []
    for code, title, group, normal in codes:
        spec = f'"{code}": ("{title}", "{group}", "{normal}")'
        matching = re.findall(rf'(?m)^\s*["\']{code}["\']\s*:\s*[^\n]+', s)
        if matching:
            if len(matching) != 1 or spec not in matching[0]:
                stop(f'Chart of Accounts has a different existing meaning for {code}.')
        else:
            to_add.append(f'    {spec},\n')
    if to_add:
        s = once(s, 'ACCOUNT_SPECS = {\n', 'ACCOUNT_SPECS = {\n' + ''.join(to_add), name)
    proposed[name] = s

    # URLs: only insert named routes that do not already exist, never replace api/urls.py.
    name = 'api/urls.py'
    s = proposed[name]
    for module in ('egg_pos_admin_expenses', 'egg_pos_payroll'):
        if not re.search(rf'(?m)^from api\.views import {module}\s*$', s):
            m = re.search(r'(?m)^urlpatterns\s*=\s*\[', s)
            if not m:
                stop('Cannot locate urlpatterns in api/urls.py')
            s = s[:m.start()] + f'from api.views import {module}\n' + s[m.start():]
    new_routes = [
        ('egg_pos_admin_expenses', '    path("egg-pos/admin-expenses/", egg_pos_admin_expenses.admin_expenses, name="egg_pos_admin_expenses"),\n'),
        ('egg_pos_void_admin_expense', '    path("egg-pos/admin-expenses/<int:expense_id>/void/", egg_pos_admin_expenses.void_admin_expense, name="egg_pos_void_admin_expense"),\n'),
        ('egg_pos_payroll', '    path("egg-pos/payroll/", egg_pos_payroll.payroll, name="egg_pos_payroll"),\n'),
    ]
    pending = []
    for route_name, definition in new_routes:
        n = len(re.findall(rf'name\s*=\s*["\']{re.escape(route_name)}["\']', s))
        if n > 1:
            stop(f'api/urls.py already has duplicate route {route_name}.')
        if n == 0:
            pending.append(definition)
    if pending:
        m = re.search(r'(?m)^urlpatterns\s*=\s*\[', s)
        if not m:
            stop('Cannot locate urlpatterns for new routes.')
        s = s[:m.end()] + '\n' + ''.join(pending) + s[m.end():]
    try:
        ast.parse(s, filename=name)
    except SyntaxError as exc:
        stop(f'URL patch would not parse: {exc}')
    proposed[name] = s

    # Sidebar nav moved to a separate include in newer RayNoor releases.
    # Prefer that include, and never abort an accounting repair just because
    # a navigational link has been restructured.
    admin_link = ('{% if request.user.is_superuser or request.user.is_staff %}'
                  '<a href="{% url \'egg_pos_admin_expenses\' %}" '
                  'class="{% if request.resolver_match.url_name == \'egg_pos_admin_expenses\' %}active{% endif %}">'
                  '<i class="fa-solid fa-building"></i><span>Admin Expenses</span></a>{% endif %}')
    payroll_link = ('{% if request.user.is_superuser or request.user.is_staff %}'
                    '<a href="{% url \'egg_pos_payroll\' %}" '
                    'class="{% if request.resolver_match.url_name == \'egg_pos_payroll\' %}active{% endif %}">'
                    '<i class="fa-solid fa-money-bill-wave"></i><span>Staff Salaries</span></a>{% endif %}')
    menu_files = [n for n in (
        'api/templates/api/includes/egg_pos_sidebar_nav.html',
        'api/templates/api/egg_pos_base.html',
    ) if n in proposed]
    menu_fixed = False
    for menu_file in menu_files:
        s = proposed[menu_file]
        needs_admin = "{% url 'egg_pos_admin_expenses' %}" not in s and '{% url "egg_pos_admin_expenses" %}' not in s
        needs_payroll = "{% url 'egg_pos_payroll' %}" not in s and '{% url "egg_pos_payroll" %}' not in s
        if not needs_admin and not needs_payroll:
            menu_fixed = True
            break
        # Find the entire Fuel & Expenses link; newer sidebars wrap the text in span.
        fuel_link = re.search(
            r"<a\b[^>]*href=[\"']\{%\s*url\s*[\"']egg_pos_expenses[\"']\s*%\}[\"'][^>]*>.*?Fuel\s*&\s*Expenses\s*(?:</span>)?\s*</a>",
            s, flags=re.S,
        )
        if not fuel_link:
            continue
        links = []
        # Do not insert duplicate routes into this template.
        if needs_admin:
            links.append(admin_link)
        if needs_payroll:
            links.append(payroll_link)
        s = s[:fuel_link.end()] + '\n' + '\n'.join(links) + s[fuel_link.end():]
        proposed[menu_file] = s
        menu_fixed = True
        break
    if not menu_fixed:
        print('INFO: Sidebar layout changed; no menu links inserted. Features remain available through URLs and Fuel & Expenses.')

    name = 'api/templates/api/egg_pos_expenses.html'
    s = proposed[name]
    if 'Record Rent, Stationery & Admin Expenses' not in s:
        needle = '{% block egg_pos_content %}'
        s = once(s, needle,
                 needle + '\n{% if request.user.is_superuser or request.user.is_staff %}'
                 '<div style="margin-bottom:12px"><a class="ep-btn primary" href="{% url \'egg_pos_admin_expenses\' %}">'
                 '<i class="fa-solid fa-building"></i> Record Rent, Stationery & Admin Expenses</a></div>'
                 '{% endif %}\n', name)
    proposed[name] = s

    name = 'api/services/egg_pos_accounting.py'
    s = proposed[name]
    missing = []
    if 'rebuild_admin_expenses()' not in s:
        missing.append('    from api.services.egg_pos_admin_expenses import rebuild_admin_expenses\n    rebuild_admin_expenses()\n')
    if 'rebuild_staff_payroll()' not in s:
        missing.append('    from api.services.egg_pos_payroll import rebuild_staff_payroll\n    rebuild_staff_payroll()\n')
    if missing:
        anchor = 'def sync_all_egg_pos_books():\n'
        if anchor not in s:
            stop(f'{name}: no sync_all_egg_pos_books function to hook into.')
        rest = s.split(anchor, 1)[1]
        if rest.startswith('    """'):
            end = rest.find('"""', len('    """'))
            if end < 0:
                stop(f'{name}: malformed function docstring.')
            needle = anchor + rest[:end + 3] + '\n'
        else:
            needle = anchor
        s = once(s, needle, needle + ''.join(missing), name)
    proposed[name] = s

    # Prevent newly posted commission or new commission advances after salary starts.
    name = 'api/views/egg_pos.py'
    s = proposed[name]
    if 'Salary starts {salary_from:%d %b %Y}' not in s:
        old = '                if period_end > today:\n                    raise ValueError("Cannot finalize commission for future dates.")'
        new = old + '''
                from api.services.egg_pos_payroll import salary_cutoff
                salary_from = salary_cutoff(salesperson.pk)
                if salary_from is not None and period_end >= salary_from:
                    raise ValueError(
                        f"Salary starts {salary_from:%d %b %Y} for this employee. "
                        "Do not post overlapping commission. Historical commission remains payable."
                    )'''
        s = once(s, old, new, name + ': commission-post guard')
    if 'This employee has switched to salary.' not in s:
        old = '                payment, applied, repeated = record_commission_cash_payment('
        new = '''                from api.services.egg_pos_payroll import salary_cutoff
                if salary_cutoff(salesperson.pk) is not None:
                    from api.models.egg_pos import EggPOSCommissionPeriod as _SalarySwitchCommissionPeriod
                    remaining_commission = sum(
                        (p.balance_due for p in _SalarySwitchCommissionPeriod.objects.filter(salesperson=salesperson)),
                        Decimal('0.00'),
                    )
                    amount_to_pay = _parse_money(request.POST.get("amount"), "Commission payment")
                    if amount_to_pay > remaining_commission:
                        raise ValueError(
                            f"This employee has switched to salary. Only Rs {remaining_commission:,.2f} "
                            "historical commission is payable; new commission advances are disabled."
                        )
                payment, applied, repeated = record_commission_cash_payment('''
        s = once(s, old, new, name + ': commission-advance guard')
    try:
        ast.parse(s, filename=name)
    except SyntaxError as exc:
        stop(f'Commission view would not parse: {exc}')
    proposed[name] = s

    changes = [n for n in SHARED if proposed[n] != current[n]]
    additions = [(feature, n) for feature, names in [('admin', ADMIN_NEW), ('salary', SALARY_NEW)]
                 for n in names if not (ROOT / n).exists()]
    if not changes and not additions:
        print('Already complete: no files changed.')
        return
    backup_dir = ROOT / 'raynoor_recovery_backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup_dir.mkdir(parents=True, exist_ok=False)
    for n in changes:
        b = backup_dir / n
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / n, b)
    # All code preflighted. Restore shared-file backups on any write failure.
    written = []
    try:
        for n in changes:
            (ROOT / n).write_text(proposed[n], encoding='utf-8')
            written.append(n)
            print('Integrated:', n)
        for feature, n in additions:
            src, dst = PAYLOAD / feature / n, ROOT / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print('Added:', n)
    except Exception as exc:
        for n in written:
            shutil.copy2(backup_dir / n, ROOT / n)
        print('Restored modified shared files from backups.')
        stop(f'Failed to write files: {exc}. Check any newly added files manually.')

    print('\nRecovered partial Admin Expenses + Staff Salaries integration.')
    print('Shared-file backups:', backup_dir)
    print('No database access, no migration performed, and no historical transactions changed.')
    print('NEXT, run:')
    print('    python manage.py check')
    print('    python manage.py makemigrations --check --dry-run')
    print('    python manage.py showmigrations api')
    print('    python manage.py test api.admin_expense_tests api.salary_payroll_tests')
    print('If checks pass, start locally; 0084/0085 already applied if showmigrations marks them [X].')


if __name__ == '__main__':
    do()
