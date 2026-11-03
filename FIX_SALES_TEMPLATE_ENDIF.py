"""Restore the missing endif in the RayNoor repeat-customer Sales report.

Run from the directory containing manage.py, with this script in that directory.
Touches ONLY api/templates/api/egg_pos_sales.html; makes a timestamped backup.
No migrations or database changes.
"""
from pathlib import Path
from datetime import datetime
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'api' / 'templates' / 'api' / 'egg_pos_sales.html'
MARKER = '{# RAYNOOR_REPEAT_CUSTOMER_SOURCES_V1 #}'
NEXT_SECTION = '<section class="ep-panel ep-sales-panel">'


def fail(message):
    raise SystemExit('STOP (no files changed): ' + message)


def patched(source):
    if source.count(MARKER) != 1:
        fail('Expected exactly one Repeat Customer report marker in egg_pos_sales.html.')
    if source.count(NEXT_SECTION) != 1:
        fail('Cannot uniquely identify the existing Sales list section. No changes made.')

    marker_at = source.index(MARKER)
    section_at = source.index(NEXT_SECTION)
    if section_at <= marker_at:
        fail('The Sales list is not after the acquisition report; template layout differs.')

    report = source[marker_at:section_at]
    if len(report) > 5000:
        fail('Unexpectedly large acquisition-report region; refusing speculative changes.')
    if len(re.findall(r'{%\s*if\s+can_manage_pos\s*%}', report)) != 1:
        fail('Cannot identify the report permission conditional uniquely.')

    # The acquisition panel is a distinct section inserted immediately before
    # the original Sales list. Nested if blocks are not expected here.
    if len(re.findall(r'{%\s*if\b', report)) != 1:
        fail('Unexpected nested conditional in report; manual inspection is needed.')

    if len(re.findall(r'{%\s*endif\s*%}', report)) == 1:
        print('Already fixed: acquisition report has a closing endif. No changes made.')
        return source
    if re.search(r'{%\s*endif\b', report):
        fail('Unexpected endif tag format in report.')

    # Confirm we are closing the report section, not an unrelated tag.
    match = re.search(r'(</section>)(\s*)\Z', report)
    if not match:
        fail('Cannot find the acquisition report closing </section> directly before Sales list.')
    newline = '\r\n' if '\r\n' in source else '\n'
    updated_report = report[:match.end(1)] + newline + '{% endif %}' + report[match.end(1):]
    result = source[:marker_at] + updated_report + source[section_at:]
    if result.count('{% endif %}') != source.count('{% endif %}') + 1:
        fail('Unexpected template change; stopping.')
    return result


def main():
    if not (ROOT / 'manage.py').is_file():
        fail('Place this script alongside manage.py in the poultrysaas folder.')
    if not TEMPLATE.is_file():
        fail('Missing api/templates/api/egg_pos_sales.html.')
    original_bytes = TEMPLATE.read_bytes()
    try:
        original = original_bytes.decode('utf-8')
    except UnicodeDecodeError:
        fail('Sales template is not UTF-8 encoded.')
    updated = patched(original)
    if updated == original:
        return

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = TEMPLATE.with_name(TEMPLATE.name + f'.endiffix_{stamp}.bak')
    shutil.copy2(TEMPLATE, backup)
    try:
        TEMPLATE.write_bytes(updated.encode('utf-8'))
    except Exception:
        shutil.copy2(backup, TEMPLATE)
        raise
    print('FIXED: api/templates/api/egg_pos_sales.html')
    print('Backup:', backup.name)
    print('No migration required. Verify with:')
    print('python manage.py shell -c "from django.template.loader import get_template; get_template(\'api/egg_pos_sales.html\'); print(\'Sales template OK\')"')

if __name__ == '__main__':
    main()
