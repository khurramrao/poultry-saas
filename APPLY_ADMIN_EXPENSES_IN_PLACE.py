"""Targeted, repeat-safe changes. Run from project root: python APPLY_ADMIN_EXPENSES_IN_PLACE.py"""
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / 'admin_expenses_install_backups' / datetime.now().strftime('%Y%m%d_%H%M%S')


def change(name, old, new, *, check=None):
    p = ROOT / name
    if not p.is_file():
        raise RuntimeError(f'Missing project file: {p}. Do not install against an unknown project version.')
    src = p.read_text(encoding='utf-8')
    if check and check in src:
        print('Already patched:', name)
        return
    if old not in src:
        raise RuntimeError(f'Cannot locate safe insertion point in {name}. File left unchanged; merge manually.')
    if src.count(old) != 1:
        raise RuntimeError(f'Expected one insertion point in {name}, found {src.count(old)}. File left unchanged.')
    BACKUP.mkdir(parents=True, exist_ok=True)
    dest = BACKUP / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src, encoding='utf-8')
    p.write_text(src.replace(old, new, 1), encoding='utf-8')
    print('Patched:', name, '(backup saved)')


# Install only genuinely new modules; never overwrite a newer migration or source file.
PAYLOAD = ROOT / "patch_payload"
new_paths = [
    "api/models/egg_pos_admin_expense.py",
    "api/services/egg_pos_admin_expenses.py",
    "api/views/egg_pos_admin_expenses.py",
    "api/templates/api/egg_pos_admin_expenses.html",
    "api/migrations/0084_egg_pos_admin_expenses.py",
    "api/admin_expense_tests.py",
]
for name in new_paths:
    src = PAYLOAD / name
    target = ROOT / name
    if not src.is_file():
        raise RuntimeError(f"Package is incomplete: {src}")
    if target.exists() and target.read_bytes() != src.read_bytes():
        raise RuntimeError(
            f"Existing file differs: {target}. STOP: do not overwrite it. "
            "Check migration dependencies/version or merge manually."
        )
for name in new_paths:
    src = PAYLOAD / name
    target = ROOT / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
        print("Added:", name)
    else:
        print("Already installed:", name)

# Register the new model without replacing the entire models package.
change('api/models/__init__.py',
       'from .accounting import ChartOfAccount, JournalEntry, JournalLine',
       'from .accounting import ChartOfAccount, JournalEntry, JournalLine\nfrom .egg_pos_admin_expense import EggPOSAdminExpense',
       check='from .egg_pos_admin_expense import EggPOSAdminExpense')

# Chart of Accounts: classified operating expenses and a separate fixed-asset account.
new_codes = '''    "1500": ("Office Equipment / Fixed Assets", "asset", "debit"),
    "6300": ("Office / Shop Rent", "expense", "debit"),
    "6310": ("Electricity / Utilities Expense", "expense", "debit"),
    "6320": ("Internet / Phone Expense", "expense", "debit"),
    "6330": ("Stationery / Printing Expense", "expense", "debit"),
    "6340": ("Office Consumables Expense", "expense", "debit"),
    "6350": ("Repairs & Maintenance Expense", "expense", "debit"),
    "6360": ("Marketing / Advertising Expense", "expense", "debit"),
    "6370": ("Professional / Admin Fees", "expense", "debit"),
    "6380": ("Other Administrative Expense", "expense", "debit"),
'''
change('api/services/accounting.py', 'ACCOUNT_SPECS = {\n',
       'ACCOUNT_SPECS = {\n' + new_codes,
       check='"6300": ("Office / Shop Rent"')

# Independent view module — do not overwrite egg_pos.py containing commission and purchase fixes.
change('api/urls.py', 'from django.urls import path\n',
       'from django.urls import path\nfrom api.views import egg_pos_admin_expenses\n',
       check='from api.views import egg_pos_admin_expenses')
change('api/urls.py', '    path("egg-pos/expenses/", egg_pos.expenses, name="egg_pos_expenses"),',
       '''    path("egg-pos/admin-expenses/", egg_pos_admin_expenses.admin_expenses, name="egg_pos_admin_expenses"),
    path("egg-pos/admin-expenses/<int:expense_id>/void/", egg_pos_admin_expenses.void_admin_expense, name="egg_pos_void_admin_expense"),
    path("egg-pos/expenses/", egg_pos.expenses, name="egg_pos_expenses"),''',
       check='name="egg_pos_admin_expenses"')

change('api/templates/api/egg_pos_base.html',
       '''  <a href="{% url 'egg_pos_expenses' %}" class="{% if request.resolver_match.url_name == 'egg_pos_expenses' %}active{% endif %}"><i class="fa-solid fa-gas-pump"></i>Fuel & Expenses</a>''',
       '''  <a href="{% url 'egg_pos_expenses' %}" class="{% if request.resolver_match.url_name == 'egg_pos_expenses' %}active{% endif %}"><i class="fa-solid fa-gas-pump"></i>Fuel & Expenses</a>
  {% if request.user.is_superuser or request.user.is_staff %}<a href="{% url 'egg_pos_admin_expenses' %}" class="{% if request.resolver_match.url_name == 'egg_pos_admin_expenses' %}active{% endif %}"><i class="fa-solid fa-building"></i>Admin Expenses</a>{% endif %}''',
       check='>Admin Expenses</a>')

change('api/templates/api/egg_pos_expenses.html',
       '{% block egg_pos_content %}\n',
       '''{% block egg_pos_content %}
{% if request.user.is_superuser or request.user.is_staff %}<div style="margin-bottom:12px"><a class="ep-btn primary" href="{% url 'egg_pos_admin_expenses' %}"><i class="fa-solid fa-building"></i> Record Rent, Stationery & Admin Expenses</a></div>{% endif %}
''',
       check='Record Rent, Stationery & Admin Expenses')

# Keep all existing commission, online order, and void-purchase rebuild calls intact.
rebuilder = ROOT / 'api/services/egg_pos_accounting.py'
if not rebuilder.exists():
    raise RuntimeError('Missing api/services/egg_pos_accounting.py')
t = rebuilder.read_text(encoding='utf-8')
if 'rebuild_admin_expenses()' not in t:
    anchor = 'def sync_all_egg_pos_books():\n'
    if anchor not in t:
        raise RuntimeError('Cannot find existing accounting rebuild function; add rebuild_admin_expenses manually')
    # Insert after original docstring, if present.
    rest = t.split(anchor, 1)[1]
    if rest.startswith('    """'):
        end = rest.find('"""', len('    """'))
        if end < 0: raise RuntimeError('Malformed accounting rebuild docstring')
        needle = anchor + rest[:end+3] + '\n'
    else:
        needle = anchor
    change('api/services/egg_pos_accounting.py', needle,
           needle + '    from api.services.egg_pos_admin_expenses import rebuild_admin_expenses\n    rebuild_admin_expenses()\n',
           check='rebuild_admin_expenses()')
else:
    print('Already patched accounting rebuild')

print('\nSuccess. New files installed; existing files patched via narrow insertions.')
print('Backups:', BACKUP)
print('Next: python manage.py check && python manage.py makemigrations --check --dry-run && python manage.py migrate')
