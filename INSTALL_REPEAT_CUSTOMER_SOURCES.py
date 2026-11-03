"""Narrow, fail-closed RayNoor repeat-customer acquisition + order-source installer.

Extract whole ZIP beside manage.py, run python INSTALL_REPEAT_CUSTOMER_SOURCES.py.
Requires prior Manual Pre-Bookings, POS Draft and Sales Source column patches.
Changes only specific insertion points; never replaces entire existing views/templates.
"""
import ast
from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / 'repeat_source_payload'
TAG = 'RAYNOOR_REPEAT_CUSTOMER_SOURCES_V1'
NEW = [
    'api/models/egg_pos_marketing.py',
    'api/services/egg_pos_marketing.py',
    'api/migrations/0087_egg_pos_marketing_attribution.py',
    'api/management/commands/backfill_prebooking_attribution.py',
    'api/marketing_attribution_tests.py',
]
EDIT = [
    'api/models/__init__.py', 'api/views/egg_pos.py',
    'api/templates/api/egg_pos_sale_new.html',
    'api/templates/api/egg_pos_sales.html', 'api/admin.py',
]


def stop(why):
    raise SystemExit('STOP (no files changed): ' + why)


def once(content, old, new, filename):
    if content.count(old) != 1:
        stop(f'{filename} changed: expected one safe insertion point, found {content.count(old)}. '
             'Share your latest source for a targeted merge.')
    return content.replace(old, new, 1)


def model_import(s):
    if f'# {TAG}' in s:
        return s
    return s.rstrip() + f'\n\n# {TAG}\nfrom .egg_pos_marketing import EggPOSMarketingCustomer, EggPOSMarketingSale\n'


def admin_import(s):
    if f'# {TAG}' in s:
        return s
    return s.rstrip() + f'''\n\n# {TAG}: Django Admin keeps a standard audit log of source corrections.
from api.models.egg_pos_marketing import EggPOSMarketingCustomer, EggPOSMarketingSale

@admin.register(EggPOSMarketingCustomer)
class EggPOSMarketingCustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone_key", "acquired_from", "pos_customer", "acquired_at")
    list_filter = ("acquired_from",)
    search_fields = ("name", "phone_key")
    readonly_fields = ("name", "name_key", "phone_key", "pos_customer", "created_by", "created_at", "acquired_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(EggPOSMarketingSale)
class EggPOSMarketingSaleAdmin(admin.ModelAdmin):
    list_display = ("sale", "order_source", "marketing_customer", "created_at")
    list_filter = ("order_source",)
    search_fields = ("sale__sale_number", "marketing_customer__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
'''


def view_patch(s):
    if f'# {TAG}' in s:
        return s
    if 'PREBOOK_UPGRADE_20261103' not in s or '# RAYNOOR_POS_SALES_SOURCE_COLUMN_V1' not in s:
        stop('Please install the previous pre-booking POS Draft and Sales Source column updates first.')
    filename = 'api/views/egg_pos.py'
    old = ('                    complete_with_sale(prebooking_sale, sale, request.user)\n'
           '\n            messages.success(')
    new = (f'                    complete_with_sale(prebooking_sale, sale, request.user)\n'
           f'\n                # {TAG}: save source AFTER actual invoice creation, in same DB transaction.\n'
           '                from api.services.egg_pos_marketing import record_marketing_sale\n'
           '                record_marketing_sale(sale, prebooking_sale,\n'
           '                                      (request.POST.get("order_source") or "direct").strip(), request.user)\n'
           '\n            messages.success(')
    s = once(s, old, new, filename)
    old = '    return render(request, "api/egg_pos_sales.html", context)'
    new = ('    # Customer acquisition is separate from this invoice\'s order source.\n'
           '    from api.services.egg_pos_marketing import decorate_sales, acquisition_report\n'
           '    context["sales"] = decorate_sales(context["sales"])\n'
           '    if manage:\n'
           '        context["marketing_acquisition_report"] = acquisition_report()\n'
           + old)
    return once(s, old, new, filename)


def sale_form_patch(s):
    if f'{{# {TAG} #}}' in s:
        return s
    if 'PREBOOK_UPGRADE_20261103' not in s:
        stop('POS sale template is missing the pre-booking draft upgrade.')
    old = '                    <div class="pos-field pos-sale-date-box">'
    new = f'''                    {{# {TAG} #}}
                    <div class="pos-field" style="margin:14px 0">
                      <label for="orderSource">Order received via</label>
                      <select class="pos-select" id="orderSource" name="order_source" aria-label="Order source">
                        <option value="direct">Direct / Walk-in</option>
                        <option value="tiktok">TikTok</option>
                        <option value="facebook">Facebook</option>
                        <option value="instagram">Instagram</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="phone">Phone Call</option>
                        <option value="other">Other</option>
                      </select>
                      <small style="color:#60778b">Choose where this order came from. The customer's original acquisition platform is tracked separately. For linked pre-bookings, the original booking source is used automatically.</small>
                    </div>

''' + old
    # Fix literal DTL marker, not JavaScript/curly braces.
    new = new.replace('{# RAYNOOR', '{# RAYNOOR')
    return once(s, old, new, 'api/templates/api/egg_pos_sale_new.html')


def sales_list_patch(s):
    if f'{{# {TAG} #}}' in s:
        return s
    if '{# RAYNOOR_POS_SALES_SOURCE_COLUMN_V1 #}' not in s:
        stop('Sales template is missing the previous Sales Source column update.')
    filename = 'api/templates/api/egg_pos_sales.html'
    # Add a row underneath source tag in the existing Sales table; no other columns changed.
    old = '          <td><span class="ep-origin-tag source-{{ sale.raynoor_origin_key }}">{{ sale.raynoor_origin_label }}</span></td>'
    new = ('          <td><span class="ep-origin-tag source-{{ sale.raynoor_origin_key }}">{{ sale.raynoor_origin_label }}</span>'
           '{% if sale.raynoor_acquired_label != "Untracked" %}'
           '<div style="font-size:9px;color:#627389;margin-top:5px">Acquired: <b>{{ sale.raynoor_acquired_label }}</b>'
           '{% if sale.raynoor_repeat_customer %} · Repeat{% endif %}</div>{% endif %}</td>')
    s = once(s, old, new, filename)
    old = '            <span class="ep-origin-tag source-{{ sale.raynoor_origin_key }}" style="margin-top:7px">{{ sale.raynoor_origin_label }}</span>'
    new = (old + '{% if sale.raynoor_acquired_label != "Untracked" %}'
           '<div style="font-size:9px;color:#627389;margin-top:4px">Acquired: {{ sale.raynoor_acquired_label }}'
           '{% if sale.raynoor_repeat_customer %} · Repeat{% endif %}</div>{% endif %}')
    s = once(s, old, new, filename)
    old = '<section class="ep-panel ep-sales-panel">'
    panel = '''{# RAYNOOR_REPEAT_CUSTOMER_SOURCES_V1 #}
{% if can_manage_pos %}
<section class="ep-panel" style="padding:15px 17px;margin-bottom:14px">
 <strong style="color:#183e62">Customer Acquisition — Tracked POS Sales</strong>
 <p style="font-size:11px;color:#657b8e;margin:6px 0 12px">Revenue includes completed, non-reversed POS invoices linked to an identified customer after this update or a verified pre-booking backfill. Older unlinked sales are excluded; order source is shown separately above.</p>
 <div style="overflow-x:auto"><table class="ep-table" style="width:100%;font-size:11px">
 <thead><tr><th>Originally from</th><th>Customers</th><th>Invoices</th><th>Repeat orders</th><th>Invoiced value</th></tr></thead>
 <tbody>{% for row in marketing_acquisition_report %}
 <tr><td>{{ row.label }}</td><td>{{ row.customers|intcomma }}</td><td>{{ row.orders|intcomma }}</td><td>{{ row.repeat_orders|intcomma }}</td><td>Rs {{ row.revenue|floatformat:0|intcomma }}</td></tr>
 {% endfor %}</tbody></table></div>
</section>
''' + old
    return once(s, old, panel, filename)


def install():
    if not (ROOT/'manage.py').is_file():
        stop('Extract the entire ZIP beside manage.py and run from the project folder.')
    required = EDIT + ["api/models/egg_pos_prebooking.py", "api/services/egg_pos_prebooking_sale.py"]
    for name in required:
        if not (ROOT/name).is_file():
            stop('Missing prerequisite: ' + name)
    if not (ROOT/'api/migrations/0086_egg_pos_manual_prebookings.py').is_file():
        stop('Manual pre-bookings migration 0086 is missing.')
    for p in (ROOT/'api/migrations').glob('0087*.py'):
        if p.name != '0087_egg_pos_marketing_attribution.py':
            stop('Another migration 0087 already exists. Do not overwrite; merge migration branches first.')
    for name in NEW:
        file_in_zip, target = PAYLOAD/name, ROOT/name
        if not file_in_zip.is_file():
            stop('Incomplete ZIP: ' + name)
        if target.is_file() and target.read_bytes() != file_in_zip.read_bytes():
            stop(name + ' already exists and differs; will not overwrite.')
    originals = {name: (ROOT/name).read_text(encoding='utf-8') for name in EDIT}
    methods = {
        'api/models/__init__.py': model_import,
        'api/views/egg_pos.py': view_patch,
        'api/templates/api/egg_pos_sale_new.html': sale_form_patch,
        'api/templates/api/egg_pos_sales.html': sales_list_patch,
        'api/admin.py': admin_import,
    }
    wanted = {name: method(originals[name]) for name, method in methods.items()}
    for name in ('api/views/egg_pos.py', 'api/models/__init__.py', 'api/admin.py'):
        try: ast.parse(wanted[name], filename=name)
        except SyntaxError as e: stop(f'Python syntax error in proposed {name}: {e}')
    edits = [name for name in EDIT if originals[name] != wanted[name]]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    copied = []
    backups = []
    try:
        # Make the optional management command importable in older Django apps.
        for pkg in ('api/management/__init__.py', 'api/management/commands/__init__.py'):
            path = ROOT/pkg
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('', encoding='utf-8')
                copied.append(path)
                print('Added:', pkg)
        for name in NEW:
            dst = ROOT/name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PAYLOAD/name, dst)
                copied.append(dst)
                print('Added:', name)
        for name in edits:
            path = ROOT/name
            backup = path.with_name(path.name + f'.marketing_{timestamp}.bak')
            shutil.copy2(path, backup)
            backups.append((path, backup))
            path.write_text(wanted[name], encoding='utf-8')
            print('Updated:', name, '(backup saved)')
    except Exception as e:
        for dst, backup in backups: shutil.copy2(backup, dst)
        for dst in copied: dst.unlink(missing_ok=True)
        stop('Write failed; restored originals. Error: '+str(e))
    print('SUCCESS. No sale, payment, stock, or customer records were changed.')
    print('Next: python manage.py check')
    print('Next: python manage.py makemigrations --check --dry-run')
    print('After backup: python manage.py migrate (migration 0087)')
    print('Then: python manage.py test api.marketing_attribution_tests')
    print('Optional old-linked invoice dry-run: python manage.py backfill_prebooking_attribution')

if __name__ == '__main__':
    install()
