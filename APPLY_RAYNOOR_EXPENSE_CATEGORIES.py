#!/usr/bin/env python3
"""Apply additive, repeat-safe Retail POS category updates. Run beside manage.py.

No original files are supplied in this ZIP: edits are made to your actual current project.
"""
from pathlib import Path
from datetime import datetime
import sys

ROOT = Path(__file__).resolve().parent
FILES = [
    Path('api/models/egg_pos_admin_expense.py'),
    Path('api/services/egg_pos_admin_expenses.py'),
    Path('api/services/accounting.py'),
]
TEMPLATE = Path('api/templates/api/egg_pos_admin_expenses.html')


def insert_after(text, anchor, new_line, marker):
    if marker in text:
        return text
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f'Expected one insertion anchor for {marker!r}; found {count}. Nothing changed.')
    return text.replace(anchor, anchor + new_line, 1)


def modify(path, source):
    if str(path) == str(FILES[0]):
        source = insert_after(source,
            '        ("supplies", "Office Consumables"),\n',
            '        ("packing_material", "Packing Material"),\n',
            '("packing_material", "Packing Material")')
        source = insert_after(source,
            '        ("marketing", "Marketing / Advertising"),\n',
            '        ("social_media_marketing", "Social Media Marketing"),\n',
            '("social_media_marketing", "Social Media Marketing")')
        source = insert_after(source,
            '        ("delivery", "Customer Delivery / Courier Charges"),\n',
            '        ("stock_transport", "Stock Transport Charges"),\n',
            '("stock_transport", "Stock Transport Charges")')
    elif str(path) == str(FILES[1]):
        source = insert_after(source,
            '    "supplies": "6340",\n',
            '    "packing_material": "6130",  # Packaging material used for retail sales.\n',
            '"packing_material": "6130"')
        source = insert_after(source,
            '    "marketing": "6360",\n',
            '    "social_media_marketing": "6365",  # Distinct social campaign expense.\n',
            '"social_media_marketing": "6365"')
        source = insert_after(source,
            '    "delivery": "6110",  # Outbound delivery paid by RayNoor; selling expense (never stock cost).\n',
            '    "stock_transport": "6160",  # General stock movement; not inbound freight already capitalized in purchases.\n',
            '"stock_transport": "6160"')
    elif str(path) == str(FILES[2]):
        source = insert_after(source,
            '    "6360": ("Marketing / Advertising Expense", "expense", "debit"),\n',
            '    "6365": ("Social Media Marketing Expense", "expense", "debit"),\n',
            '"6365": ("Social Media Marketing Expense"')
        source = insert_after(source,
            '    "6130": ("Packaging Expense", "expense", "debit"),\n',
            '    "6160": ("Stock Transport Expense", "expense", "debit"),\n',
            '"6160": ("Stock Transport Expense"')
    return source


def main():
    if not (ROOT / 'manage.py').exists():
        sys.exit('ERROR: Extract ZIP in the same folder as manage.py, then run from there.')
    changes = {}
    for p in FILES:
        f = ROOT / p
        if not f.exists():
            sys.exit(f'ERROR: Missing {p}; stopping without changing any file.')
        old = f.read_text(encoding='utf-8')
        try:
            new = modify(p, old)
        except RuntimeError as exc:
            sys.exit(f'ERROR processing {p}: {exc}')
        if old != new:
            changes[f] = (old, new)
    # Optional explanatory UI - only when the existing delivery help widget exists.
    ui = ROOT / TEMPLATE
    if ui.exists():
        old = ui.read_text(encoding='utf-8')
        if 'id="deliveryCategoryHelp"' in old and 'id="packingMaterialCategoryHelp"' not in old:
            anchor = '          <div class="ep-adm-category-help ep-adm-wide" id="deliveryCategoryHelp"'
            idx = old.find(anchor)
            end = old.find('</div>', idx)
            if idx < 0 or end < 0:
                sys.exit('ERROR: Cannot safely find admin category help in template.')
            end += len('</div>')
            extra = '''
          <div class="ep-adm-category-help ep-adm-wide" id="packingMaterialCategoryHelp" role="note" hidden><strong>Packing Material:</strong> Record disposable retail bags, cartons, labels, and other packaging used in selling goods. Do not enter the same amount if it has already been included in the jar/lot purchase cost.</div>
          <div class="ep-adm-category-help ep-adm-wide" id="stockTransportCategoryHelp" role="note" hidden><strong>Stock Transport:</strong> Use only for stock movement costs not already included in a Vendor Purchase or Farm Transfer. Freight to bring a specific purchase into inventory belongs in its Inbound Transport field, not here.</div>
          <div class="ep-adm-category-help ep-adm-wide" id="socialMediaCategoryHelp" role="note" hidden><strong>Social Media Marketing:</strong> Enter TikTok, Facebook, Instagram, creator/agency and campaign advertising charges. Add the campaign or platform in Notes.</div>'''
            old2 = old[:end] + extra + old[end:]
            old_script = "  function updateHelp() { note.hidden = select.value !== 'delivery'; }"
            new_script = '''  const extraHelp = {
    packing_material: document.getElementById('packingMaterialCategoryHelp'),
    stock_transport: document.getElementById('stockTransportCategoryHelp'),
    social_media_marketing: document.getElementById('socialMediaCategoryHelp'),
  };
  function updateHelp() {
    note.hidden = select.value !== 'delivery';
    Object.entries(extraHelp).forEach(([category, el]) => { if (el) el.hidden = select.value !== category; });
  }'''
            if old_script not in old2:
                sys.exit('ERROR: Existing category help script changed. Not editing automatically.')
            new = old2.replace(old_script, new_script, 1)
            changes[ui] = (old, new)
    if not changes:
        print('Already installed; no changes necessary.')
        return
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_root = ROOT / 'raynoor_expense_category_backups' / stamp
    for f, (old, new) in changes.items():
        rel = f.relative_to(ROOT)
        b = backup_root / rel
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(old, encoding='utf-8')
    for f, (old, new) in changes.items():
        f.write_text(new, encoding='utf-8')
        print('Updated:', f.relative_to(ROOT))
    print('Backups:', backup_root.relative_to(ROOT))
    print('Next: python manage.py check && python manage.py makemigrations --check --dry-run && python manage.py migrate')


if __name__ == '__main__':
    main()
