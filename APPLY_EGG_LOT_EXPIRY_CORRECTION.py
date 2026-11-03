"""Safe, repeatable installer for RayNoor Egg POS expiry-date correction.

Place this script and the supplied api/ files beside manage.py. Run:
    python APPLY_EGG_LOT_EXPIRY_CORRECTION.py

It changes ONLY existing api/urls.py and api/templates/api/egg_pos_inventory.html,
and creates no new migrations or database records. It refuses to guess where to
insert code if your current application diverges from the expected anchors.
"""
import ast
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URLS = ROOT / 'api' / 'urls.py'
INVENTORY = ROOT / 'api' / 'templates' / 'api' / 'egg_pos_inventory.html'
MODULE = ROOT / 'api' / 'views' / 'egg_pos_expiry.py'
TEMPLATE = ROOT / 'api' / 'templates' / 'api' / 'egg_pos_lot_expiry.html'


def patch_urls(data):
    # Import and route registration must both be present; never copy older URL files.
    if not re.search(r'^from api\.views import egg_pos_expiry\s*$', data, re.M):
        anchor = re.search(r'^urlpatterns\s*=\s*\[', data, re.M)
        if not anchor:
            raise RuntimeError('Could not find urlpatterns in api/urls.py; no files changed.')
        data = data[:anchor.start()] + 'from api.views import egg_pos_expiry\n\n' + data[anchor.start():]
    if not re.search(r'name\s*=\s*["\']egg_pos_lot_expiry["\']', data):
        anchor = re.search(
            r'(?m)^\s*path\(["\']egg-pos/inventory/["\'],\s*egg_pos\.inventory,\s*name=["\']egg_pos_inventory["\']\),\s*$',
            data,
        )
        if not anchor:
            raise RuntimeError('Could not find the Egg POS inventory route; no files changed.')
        indent = re.match(r'\s*', anchor.group(0)).group(0).replace('\n','')
        route = (
            indent + 'path("egg-pos/inventory/lots/<int:lot_id>/expiry/", '
            'egg_pos_expiry.correct_lot_expiry, name="egg_pos_lot_expiry"),\n'
        )
        data = data[:anchor.end()] + '\n' + route + data[anchor.end():]
    ast.parse(data, filename=str(URLS))
    return data


def patch_inventory(data):
    # Do not mistake sellable stock for physical stock. Expired stock remains on hand.
    data = data.replace('eggs physically in stock', 'eggs currently sellable')
    if 'Expiry-held stock:' not in data:
        anchor = re.search(r'<div class="inv-note">Dozen, tray and crate are alternative views.*?</div>', data)
        if not anchor:
            raise RuntimeError('Inventory stock notes changed; cannot insert expired-stock summary safely.')
        new_note = (
            '\n  <div class="inv-note" style="color:#9b551c">Expiry-held stock: '
            '<strong>{{ row.expired_stock|intcomma }} eggs</strong> · '
            'Not included in sellable stock. Review recorded dates and physical egg condition before sale.'
            '</div>\n'
        )
        data = data[:anchor.end()] + new_note + data[anchor.end():]
    if "'egg_pos_lot_expiry'" not in data:
        marker = '<div class="lot-edit">'
        start = data.find(marker)
        if start < 0 or data.find(marker, start+1) >= 0:
            raise RuntimeError('Expected exactly one FIFO lot action template; no files changed.')
        end = data.find('</div>', start)
        if end < 0:
            raise RuntimeError('Unclosed FIFO lot actions; no files changed.')
        action = (
            '{% if user.is_superuser or user.is_staff %} '
            '<a class="ep-btn" href="{% url \'egg_pos_lot_expiry\' lot.id %}" '
            'title="Correct recorded expiry date, with admin password and audit">Correct Expiry</a>'
            '{% endif %}'
        )
        data = data[:end] + action + data[end:]
    return data


def run():
    for f in [URLS, INVENTORY, MODULE, TEMPLATE]:
        if not f.is_file():
            raise RuntimeError(f'Missing {f.relative_to(ROOT)}. Extract the complete patch next to manage.py.')
    original = {URLS: URLS.read_text(encoding='utf-8'), INVENTORY: INVENTORY.read_text(encoding='utf-8')}
    modified = {URLS: patch_urls(original[URLS]), INVENTORY: patch_inventory(original[INVENTORY])}
    changes = [path for path in original if original[path] != modified[path]]
    if not changes:
        print('Already installed: no changes made.')
        return
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    for path in changes:
        backup = path.with_name(path.name + '.expiryfix_' + stamp + '.bak')
        shutil.copy2(path, backup)
        path.write_text(modified[path], encoding='utf-8')
        print(f'Patched: {path.relative_to(ROOT)} (backup: {backup.name})')
    print('Done. No database migration required. Run Django check and expiry correction tests.')


if __name__ == '__main__':
    run()
