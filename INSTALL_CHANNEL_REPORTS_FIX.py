"""Targeted installer for 3 distinct RayNoor Sales marketing reports.

Run from the project root, beside manage.py. No database migration.
Never replaces the entire Sales view or template; no model/accounting edits.
"""
from pathlib import Path
from datetime import datetime
import ast
import re
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / 'channel_reports_payload'
VIEW = ROOT / 'api/views/egg_pos.py'
TEMPLATE = ROOT / 'api/templates/api/egg_pos_sales.html'
FILES = [
    'api/services/egg_pos_channel_reports.py',
    'api/channel_reporting_tests.py',
]
IDENTITY = '{# RAYNOOR_CHANNEL_REPORTS_V2 #}'
EXISTING = '{# RAYNOOR_REPEAT_CUSTOMER_SOURCES_V1 #}'
SALES_SECTION = '<section class="ep-panel ep-sales-panel">'


def fail(message):
    raise SystemExit('STOP (no files changed): ' + message)


def style():
    return '''
  /* RAYNOOR_CHANNEL_REPORTS_V2: read-only marketing reporting */
  .ep-channel-reports{display:grid;gap:10px;margin:0 0 14px}
  .ep-channel-report{border:1px solid #dfe8f1;border-radius:14px;background:#fff;overflow:hidden}
  .ep-channel-report summary{list-style:none;display:flex;align-items:center;justify-content:space-between;
    gap:14px;cursor:pointer;padding:14px 16px;color:#153b61;font-size:13px;font-weight:900}
  .ep-channel-report summary::-webkit-details-marker{display:none}
  .ep-channel-report summary:after{content:'+';color:#1b67b7;font-size:18px;font-weight:700}
  .ep-channel-report[open] summary:after{content:'−'}
  .ep-channel-report .ep-channel-body{border-top:1px solid #edf1f7;padding:13px 15px 16px}
  .ep-channel-report p{color:#61758a;font-size:11px;line-height:1.55;margin:0 0 12px}
  .ep-channel-report .ep-channel-table-wrap{overflow-x:auto}
  .ep-channel-report table{width:100%;border-collapse:collapse;font-size:11px;min-width:510px}
  .ep-channel-report th{text-align:left;background:#f5f9fd;color:#506b84;letter-spacing:.02em;font-size:9px;padding:10px 12px}
  .ep-channel-report td{padding:10px 12px;border-top:1px solid #eaf0f5;color:#1c4365}
  .ep-channel-report .ep-channel-money{font-weight:850;white-space:nowrap}
  .ep-channel-report .ep-channel-total td{background:#f4f9ff;font-weight:900;border-top:2px solid #dce8f5}
  .ep-channel-kicker{display:block;color:#72849b;font-size:10px;font-weight:550;margin-top:3px}
'''


def panel():
    return '''{# RAYNOOR_REPEAT_CUSTOMER_SOURCES_V1 #}
{# RAYNOOR_CHANNEL_REPORTS_V2 #}
{% if can_manage_pos %}
<div class="ep-channel-reports">
  <details class="ep-channel-report" open>
    <summary><span>Order Source Performance <small class="ep-channel-kicker">All non-reversed POS invoices · all time</small></span></summary>
    <div class="ep-channel-body">
      <p>Counts real POS invoices by where each order originated. Unpaid invoices are included in <b>invoiced value</b> (not money collected). Website invoices require an RNWEB link; completed pre-bookings preserve their verified platform. Older unmatched invoices remain Direct / Unattributed.</p>
      <div class="ep-channel-table-wrap"><table>
        <thead><tr><th>Order source</th><th>Invoices</th><th>Invoiced value (Rs)</th></tr></thead>
        <tbody>
        {% for row in marketing_order_source_report %}
          <tr><td>{{ row.label }}</td><td>{{ row.invoices|intcomma }}</td><td class="ep-channel-money">Rs {{ row.revenue|floatformat:0|intcomma }}</td></tr>
        {% endfor %}
          <tr class="ep-channel-total"><td>Total</td><td>{{ marketing_order_source_total.invoices|intcomma }}</td><td>Rs {{ marketing_order_source_total.revenue|floatformat:0|intcomma }}</td></tr>
        </tbody>
      </table></div>
    </div>
  </details>
  <details class="ep-channel-report">
    <summary><span>Customer Acquisition <small class="ep-channel-kicker">Identified customers · original acquisition channel</small></span></summary>
    <div class="ep-channel-body">
      <p>Tracks only positively identified customer profiles. A source-labelled invoice can appear above even when this report shows zero because no verified customer profile is attached. Lifetime value includes all linked, non-reversed invoices, regardless of where a repeat order was placed.</p>
      <div class="ep-channel-table-wrap"><table>
        <thead><tr><th>Originally acquired from</th><th>Identified customers</th><th>Linked invoices</th><th>Lifetime invoiced value</th></tr></thead>
        <tbody>{% for row in marketing_acquisition_report %}
        <tr><td>{{ row.label }}</td><td>{{ row.customers|intcomma }}</td><td>{{ row.orders|intcomma }}</td><td class="ep-channel-money">Rs {{ row.revenue|floatformat:0|intcomma }}</td></tr>
        {% endfor %}</tbody>
      </table></div>
    </div>
  </details>
  <details class="ep-channel-report">
    <summary><span>Repeat Customer Revenue <small class="ep-channel-kicker">Second and later verified purchases · original platform</small></span></summary>
    <div class="ep-channel-body">
      <p>Repeat customers are matched only using verified customer profiles, not phone numbers alone. Repeat revenue is credited to the platform that first acquired the customer, even if later orders arrive by WhatsApp or direct sale. Reversed invoices are excluded.</p>
      <div class="ep-channel-table-wrap"><table>
        <thead><tr><th>Originally acquired from</th><th>Returning customers</th><th>Repeat invoices</th><th>Repeat invoiced value</th></tr></thead>
        <tbody>{% for row in marketing_repeat_report %}
        <tr><td>{{ row.label }}</td><td>{{ row.repeat_customers|intcomma }}</td><td>{{ row.repeat_orders|intcomma }}</td><td class="ep-channel-money">Rs {{ row.repeat_revenue|floatformat:0|intcomma }}</td></tr>
        {% endfor %}</tbody>
      </table></div>
    </div>
  </details>
</div>
{% endif %}
'''


def replace_template(data):
    if IDENTITY in data:
        return data
    if data.count(EXISTING) != 1 or data.count(SALES_SECTION) != 1:
        fail('Unexpected Sales report layout; need latest egg_pos_sales.html before making changes.')
    before = data.index(EXISTING)
    after = data.index(SALES_SECTION, before)
    old_panel = data[before:after]
    if not (250 < len(old_panel) < 7000):
        fail('Existing Customer Acquisition panel has unexpected length.')
    if 'Customer Acquisition — Tracked POS Sales' not in old_panel or \
            'marketing_acquisition_report' not in old_panel:
        fail('Existing Customer Acquisition panel has changed; not overwriting it.')
    # Accept the previous end-if hotfix; replace the complete panel including
    # its conditional, without editing any other Sales page sections.
    updated = data[:before] + panel() + data[after:]
    if '/* RAYNOOR_CHANNEL_REPORTS_V2:' not in updated:
        if updated.count('</style>') != 1:
            fail('Cannot safely insert style into Sales template.')
        updated = updated.replace('</style>', style() + '\n</style>', 1)
    return updated


def replace_view(data):
    marker = '# RAYNOOR_CHANNEL_REPORTS_V2'
    if marker in data:
        return data
    block = '        context["marketing_acquisition_report"] = acquisition_report()'
    if data.count(block) != 1:
        fail('Cannot find customer acquisition context insertion point. No files changed.')
    extra = '''
        # RAYNOOR_CHANNEL_REPORTS_V2: read-only, all-time reporting.
        from api.services.egg_pos_channel_reports import order_source_report, repeat_customer_report
        (context["marketing_order_source_report"],
         context["marketing_order_source_total"]) = order_source_report()
        context["marketing_repeat_report"] = repeat_customer_report()'''
    return data.replace(block, block + extra, 1)


def validate_dtl(source):
    """Check the condition/loop balance for edited template before writing it."""
    stack = []
    for match in re.finditer(r'{%\s*(if|for|endif|endfor|elif|else|empty)\b.*?%}', source, re.DOTALL):
        tag = match.group(1)
        if tag in ('if', 'for'):
            stack.append(tag)
        elif tag in ('endif', 'endfor'):
            need = {'endif':'if', 'endfor':'for'}[tag]
            if not stack or stack.pop() != need:
                fail('Sales template condition/loop mismatch; no files changed.')
        elif tag in ('elif', 'else'):
            if not stack or stack[-1] != 'if':
                fail('Sales template has unexpected elif/else; no files changed.')
        elif tag == 'empty':
            if not stack or stack[-1] != 'for':
                fail('Sales template has unexpected empty tag; no files changed.')
    if stack:
        fail('Sales template has an unclosed block; no files changed.')


def main():
    if not (ROOT / 'manage.py').is_file():
        fail('Extract the entire ZIP beside manage.py in poultrysaas.')
    if not VIEW.is_file() or not TEMPLATE.is_file():
        fail('Missing existing Egg POS Sales view or template.')
    for name in FILES:
        source = PAYLOAD / name
        destination = ROOT / name
        if not source.is_file():
            fail('ZIP incomplete, missing: ' + name)
        if destination.is_file() and destination.read_bytes() != source.read_bytes():
            fail('Different version of '+name+' already exists. No overwrite.')
    old_view, old_template = VIEW.read_text(encoding='utf-8'), TEMPLATE.read_text(encoding='utf-8')
    new_view, new_template = replace_view(old_view), replace_template(old_template)
    try:
        ast.parse(new_view, filename='api/views/egg_pos.py')
        ast.parse((PAYLOAD / FILES[0]).read_text(encoding='utf-8'))
        ast.parse((PAYLOAD / FILES[1]).read_text(encoding='utf-8'))
    except SyntaxError as exc:
        fail('Python syntax check failed: ' + str(exc))
    validate_dtl(new_template)
    originals = [(VIEW, old_view, new_view), (TEMPLATE, old_template, new_template)]
    changed = [(p, prev, new) for p, prev, new in originals if prev != new]
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    created, backed_up = [], []
    try:
        for name in FILES:
            dst, src = ROOT / name, PAYLOAD / name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                created.append(dst)
                print('Added:', name)
        for path, old, new in changed:
            backup = path.with_name(path.name + '.channelreport_' + stamp + '.bak')
            shutil.copy2(path, backup)
            backed_up.append((path, backup))
            path.write_text(new, encoding='utf-8')
            print('Patched:', path.relative_to(ROOT), '(backup saved)')
    except Exception as exc:
        for path, backup in reversed(backed_up):
            shutil.copy2(backup, path)
        for path in created:
            path.unlink(missing_ok=True)
        fail('Unable to finish; originals restored: ' + str(exc))
    print('SUCCESS: Sales page now separates order-source performance, acquisition and repeat revenue.')
    print('No migration. No invoices, payments, stock, or existing database records edited.')
    print('Verify: python manage.py check')
    print('Verify: python manage.py shell -c "from django.template.loader import get_template; get_template(\'api/egg_pos_sales.html\'); print(\'Sales template OK\')"')
    print('Test: python manage.py test api.channel_reporting_tests')

if __name__ == '__main__':
    main()
