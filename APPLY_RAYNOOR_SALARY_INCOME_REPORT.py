#!/usr/bin/env python3
"""Targeted patch: individual staff-cost and advance disclosure, no journal changes."""
from pathlib import Path
from datetime import datetime
import shutil
import sys


def one(text, old, replacement, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one integration marker, found {count}. No changes made; merge manually.')
    return text.replace(old, replacement, 1)


def main():
    root = Path.cwd()
    patch = Path(__file__).resolve().parent / 'patch_snippets'
    service = root / 'api/services/accounting.py'
    template = root / 'api/templates/api/egg_pos_income_statement.html'
    if not all(path.is_file() for path in [service, template]):
        raise RuntimeError('Run from your Poultry SaaS project folder (next to manage.py).')
    s = service.read_text(encoding='utf-8')
    t = template.read_text(encoding='utf-8')
    service_patched = '_salary_person_rows(' in s
    template_patched = 'STAFF SALARY ADVANCES — MEMO ONLY' in t
    if service_patched and template_patched:
        print('Already installed; no changes made.')
        return
    if service_patched or template_patched:
        raise RuntimeError('Partial installation found. Restore both matching backups before reapplying.')
    def snip(name):
        return (patch / name).read_text(encoding='utf-8')
    helper = snip('salary_helper.txt')
    extra = snip('return_addition.txt')
    if 'from django.contrib.auth import get_user_model' not in s:
        s = one(s, 'from django.db import transaction\n', 'from django.db import transaction\nfrom django.contrib.auth import get_user_model\n', 'Django imports')
    s = one(s, 'def income_statement(start_date, end_date):\n', helper + 'def income_statement(start_date, end_date):\n', 'income statement function')
    s = one(s, '    return {\n        "revenue_rows": revenue_rows,\n', extra + '        "revenue_rows": revenue_rows,\n', 'income statement return dictionary')
    t = one(t, snip('old_expense_markup.txt'), snip('new_expense_markup.txt'), 'expense table')
    net = '  <div class="ep-pl-highlight net"><span>NET PROFIT</span><span class="ep-pl-amount">Rs {{ statement.net_profit|floatformat:2|intcomma }}</span></div>'
    t = one(t, net, net + '\n' + snip('advance_memo_markup.txt'), 'net profit row')
    t = t.replace('Egg POS Profit &amp; Loss ·', 'RayNoor Retail POS Profit &amp; Loss ·')
    compile(s, str(service), 'exec')
    # Pre-flight succeed before touching either real file.
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = root / f'raynoor_salary_income_backup_{stamp}'
    backup.mkdir()
    for path in (service, template):
        dst = backup / path.relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    service.write_text(s, encoding='utf-8')
    template.write_text(t, encoding='utf-8')
    print('Success: only accounting.py and egg_pos_income_statement.html updated.')
    print('Backups:', backup)
    print('Next: python manage.py check; python manage.py test api.salary_income_statement_tests')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('No update installed:', str(exc), file=sys.stderr)
        sys.exit(1)
