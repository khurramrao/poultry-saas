"""Add verified origin labels to Egg POS Sales without changing database or accounting.
Run from project root: python INSTALL_SALES_SOURCE_COLUMN.py
Uses strict text anchors, preserves all existing unrelated code, and creates backups.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

ROOT = Path.cwd()
VIEW_FILE = ROOT / 'api/views/egg_pos.py'
TEMPLATE_FILE = ROOT / 'api/templates/api/egg_pos_sales.html'
MARKER_VIEW = '# RAYNOOR_POS_SALES_SOURCE_COLUMN_V1'
MARKER_TEMPLATE = '{# RAYNOOR_POS_SALES_SOURCE_COLUMN_V1 #}'

VIEW_APPEND = '''    # RAYNOOR_POS_SALES_SOURCE_COLUMN_V1
    # Only use an actual, completed manual pre-booking linked to this POS
    # invoice. An invoice entered directly has no verified social origin.
    from api.models.egg_pos_prebooking import EggPOSManualPrebooking, CHANNELS

    displayed_sales = list(context["sales"])
    sale_refs = [s.sale_number for s in displayed_sales if s.sale_number]
    prebook_sources = {}
    if sale_refs:
        for invoice_ref, channel in EggPOSManualPrebooking.objects.filter(
            status="completed", invoice_reference__in=sale_refs
        ).values_list("invoice_reference", "channel"):
            prebook_sources.setdefault(invoice_ref, []).append(channel)

    labels = dict(CHANNELS)
    for sale in displayed_sales:
        if getattr(sale, "online_order", None) is not None:
            sale.raynoor_origin_label = "Website"
            sale.raynoor_origin_key = "website"
        else:
            matches = prebook_sources.get(sale.sale_number, [])
            if len(matches) == 1 and matches[0] in labels:
                sale.raynoor_origin_label = labels[matches[0]]
                sale.raynoor_origin_key = matches[0]
            elif len(matches) > 1:
                # Do not silently attribute one sale to multiple enquiries.
                sale.raynoor_origin_label = "Multiple bookings"
                sale.raynoor_origin_key = "ambiguous"
            else:
                sale.raynoor_origin_label = "Direct / Unattributed"
                sale.raynoor_origin_key = "direct"
    context["sales"] = displayed_sales
'''

STYLE = '''  {# RAYNOOR_POS_SALES_SOURCE_COLUMN_V1 #}
  .ep-sales-table{min-width:1140px}
  .ep-origin-tag{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;
    background:#f1f5f9;color:#536478;border:1px solid #e1e9f1;font-size:9px;font-weight:850;
    white-space:nowrap;max-width:165px}
  .ep-origin-tag.source-tiktok{background:#eef0fc;color:#454da7;border-color:#e0e4ff}
  .ep-origin-tag.source-facebook{background:#ebf3ff;color:#1759a8;border-color:#d5e5ff}
  .ep-origin-tag.source-instagram{background:#fff0f7;color:#a93c72;border-color:#ffdeec}
  .ep-origin-tag.source-whatsapp{background:#eaf9ef;color:#0f7843;border-color:#d5f0de}
  .ep-origin-tag.source-website{background:#eaf8fa;color:#176c83;border-color:#d5eaf0}
  .ep-origin-tag.source-ambiguous{background:#fff5e8;color:#8b581c;border-color:#f4e2c6}
'''


def replace_one(text: str, old: str, new: str, path: Path) -> str:
    matches = text.count(old)
    if matches != 1:
        raise RuntimeError(f"STOP: expected one safe insertion point in {path}, found {matches}. No files changed.")
    return text.replace(old, new, 1)


def patched_view(text: str) -> str:
    if MARKER_VIEW in text:
        return text
    old = '    return render(request, "api/egg_pos_sales.html", context)'
    if text.count(old) != 1 or text.count('def sales(request):') != 1:
        raise RuntimeError('STOP: Sale-list view is different from the supported version. No files changed.')
    return replace_one(text, old, VIEW_APPEND + old, VIEW_FILE)


def patched_template(text: str) -> str:
    if MARKER_TEMPLATE in text:
        return text
    # Keep the rest of the page exactly as the currently installed version.
    text = replace_one(text, '  .ep-sales-table{min-width:1040px}\n', '', TEMPLATE_FILE)
    text = replace_one(text, '  @media(max-width:768px){', STYLE + '  @media(max-width:768px){', TEMPLATE_FILE)
    text = replace_one(text, '          <th>Invoice</th>\n          <th>Customer</th>',
                       '          <th>Invoice</th>\n          <th>Source</th>\n          <th>Customer</th>', TEMPLATE_FILE)
    text = replace_one(text,
        '          <td><span class="ep-sales-customer">{{ sale.customer_name|default:"Walk-in" }}</span></td>',
        '          <td><span class="ep-origin-tag source-{{ sale.raynoor_origin_key }}">{{ sale.raynoor_origin_label }}</span></td>\n'
        '          <td><span class="ep-sales-customer">{{ sale.customer_name|default:"Walk-in" }}</span></td>',
        TEMPLATE_FILE)
    text = replace_one(text,
        '            <div class="ep-sales-mobile-customer">{{ sale.customer_name|default:"Walk-in" }}</div>',
        '            <div class="ep-sales-mobile-customer">{{ sale.customer_name|default:"Walk-in" }}</div>\n'
        '            <span class="ep-origin-tag source-{{ sale.raynoor_origin_key }}" style="margin-top:7px">{{ sale.raynoor_origin_label }}</span>',
        TEMPLATE_FILE)
    text = replace_one(text, '{% if can_manage_pos %}9{% else %}7{% endif %}',
                       '{% if can_manage_pos %}10{% else %}8{% endif %}', TEMPLATE_FILE)
    return text


def main():
    if not (ROOT / 'manage.py').is_file():
        raise SystemExit('STOP: Run this from the folder containing manage.py. No files changed.')
    for p in (VIEW_FILE, TEMPLATE_FILE):
        if not p.is_file():
            raise SystemExit(f'STOP: Missing {p}. No files changed.')
    # Compute BOTH changes before touching either file.
    current = {p: p.read_text(encoding='utf-8') for p in (VIEW_FILE, TEMPLATE_FILE)}
    try:
        updates = {VIEW_FILE: patched_view(current[VIEW_FILE]),
                   TEMPLATE_FILE: patched_template(current[TEMPLATE_FILE])}
    except RuntimeError as exc:
        raise SystemExit(str(exc))
    changes = [(p, contents) for p, contents in updates.items() if contents != current[p]]
    if not changes:
        print('Already installed. No files changed.')
        return
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    for p, new_content in changes:
        backup = p.with_name(f'{p.name}.sales_source_{stamp}.bak')
        backup.write_text(current[p], encoding='utf-8')
        p.write_text(new_content, encoding='utf-8')
        print(f'Patched: {p.relative_to(ROOT)} (backup: {backup.name})')
    print('Done. No database migration required. Run Django check and open Egg POS -> Sales.')


if __name__ == '__main__':
    main()
