"""Safely connect RayNoor's SKU transfer patch to an existing project.

Run from Django project root: python APPLY_SKU_TRANSFER.py
Makes a backup of each modified *existing* file; aborts before writing if a
known insertion marker cannot be located. New files are already in the ZIP.
"""
from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PATCH_MARKER = "RAYNOOR_PURCHASE_SKU_TRANSFER_V1"


def insert_once(text, *, anchor, replacement, file_name, exact=True):
    if replacement in text:
        return text
    matches = text.count(anchor)
    if matches != 1:
        raise RuntimeError(
            f"STOP: {file_name}: expected exactly one safe anchor, found {matches}. "
            "Your installed file differs from the version used to build the patch. "
            "No existing files were changed; please upload the latest source for a merge."
        )
    return text.replace(anchor, replacement if exact else anchor + replacement, 1)


def patch_view(s):
    if PATCH_MARKER in s:
        return s
    start = s.find("def edit_purchase(request, purchase_id):")
    end = s.find("\n@login_required", start + 1)
    if start < 0 or end < 0:
        raise RuntimeError("STOP: edit_purchase() cannot be safely located.")
    block = s[start:end]
    if "revalue_writeoffs_for_lot" not in block or "damaged_units" not in block:
        raise RuntimeError("STOP: Purchase edit view is not the supported November 2026 accounting-safe version.")

    anchor = "        item.remaining_eggs = int(item.lot.quantity_remaining or 0) if item.lot_id else 0\n"
    block = insert_once(block, anchor=anchor,
        replacement=anchor + "        item.transferred_eggs = (sum(t.quantity for t in item.lot.sku_transfers_out.filter(is_reversed=False)) if item.lot_id else 0)\n",
        file_name="api/views/egg_pos.py")

    anchor = "                    affected_sales[sale.id] = sale\n\n            locked_periods = []"
    inject = """                    affected_sales[sale.id] = sale
                # All derived FIFO lots from audited SKU reclassifications must
                # be included when checking paid commission locks.
                for move in item.lot.sku_transfers_out.filter(is_reversed=False).select_related("destination_lot"):
                    for allocation in move.destination_lot.sale_allocations.all():
                        sale = allocation.sale_item.sale
                        affected_sales[sale.id] = sale

            locked_periods = []"""
    block = insert_once(block, anchor=anchor, replacement=inject,
                        file_name="api/views/egg_pos.py")

    anchor = "                    revalue_writeoffs_for_lot(lot=lot, new_unit_cost=landed_unit_cost, actor=request.user)\n\n                    for allocation in lot.sale_allocations.all():"
    inject = """                    revalue_writeoffs_for_lot(lot=lot, new_unit_cost=landed_unit_cost, actor=request.user)

                    # SKU transfers are internal movements, not new purchases.
                    # Revalue their derivative lots and any subsequent sales or
                    # wastage at the same corrected FIFO landed cost.
                    for move in lot.sku_transfers_out.filter(is_reversed=False).select_related("destination_lot"):
                        dest_lot = move.destination_lot
                        dest_lot.unit_cost = landed_unit_cost
                        dest_lot.supplier = supplier
                        dest_lot.notes = (
                            f"Reclassified from purchase #{purchase.id} / lot {lot.lot_code} | "
                            f"corrected FIFO Rs {landed_unit_cost:.6f} at {stamp}"
                        )
                        dest_lot.save(update_fields=["unit_cost", "supplier", "notes"])
                        revalue_writeoffs_for_lot(
                            lot=dest_lot, new_unit_cost=landed_unit_cost, actor=request.user
                        )
                        for allocation in dest_lot.sale_allocations.all():
                            allocation.unit_cost = landed_unit_cost
                            allocation.cogs_amount = money(Decimal(allocation.quantity) * landed_unit_cost)
                            allocation.save(update_fields=["unit_cost", "cogs_amount"])
                            affected_sale_item_ids.add(allocation.sale_item_id)

                    for allocation in lot.sale_allocations.all():"""
    block = insert_once(block, anchor=anchor, replacement=inject,
                        file_name="api/views/egg_pos.py")
    block = block.replace("    \"\"\"Admin-safe correction of purchase value/transport without changing physical stock.",
                          "    \"\"\"Admin-safe correction of purchase value/transport without changing physical stock.")
    block = block + f"\n# {PATCH_MARKER}: transferred FIFO lots are revalued with their source purchase.\n"
    return s[:start] + block + s[end:]


def patch_template(s):
    if PATCH_MARKER in s:
        return s
    anchor = '<a class="ep-btn" href="{% url \'egg_pos_purchase_list\' %}"><i class="fa-solid fa-arrow-left"></i> Purchase History</a>'
    replacement = '<span style="display:flex;gap:8px;flex-wrap:wrap"><a class="ep-btn" href="{% url \'egg_pos_sku_transfer\' purchase.id %}"><i class="fa-solid fa-right-left"></i> Transfer to Another SKU</a>' + anchor + '</span>'
    s = insert_once(s, anchor=anchor, replacement=replacement,
                    file_name="api/templates/api/egg_pos_purchase_edit.html")
    anchor = '{{ item.damaged_units|intcomma }} damaged/wasted · {{ item.remaining_eggs|intcomma }} remaining'
    replacement = '{{ item.damaged_units|intcomma }} damaged/wasted · {{ item.transferred_eggs|intcomma }} transferred to other SKU · {{ item.remaining_eggs|intcomma }} remaining'
    s = insert_once(s, anchor=anchor, replacement=replacement,
                    file_name="api/templates/api/egg_pos_purchase_edit.html")
    s = s.replace('{% endblock %}',f'<!-- {PATCH_MARKER} -->\n{{% endblock %}}',1)
    return s


def main():
    files = {
        "api/models/__init__.py": lambda s: s if "from .egg_pos_sku_transfer import EggPOSSkuTransfer" in s else s.rstrip() + "\n\n# Registered internal SKU reclassification model.\nfrom .egg_pos_sku_transfer import EggPOSSkuTransfer\n",
        "api/urls.py": lambda s: s if "egg_pos_sku_transfer.purchase_sku_transfer" in s else s.rstrip() + "\n\n# RAYNOOR_PURCHASE_SKU_TRANSFER_V1\nfrom api.views import egg_pos_sku_transfer\nurlpatterns += [\n    path(\"egg-pos/purchases/<int:purchase_id>/sku-transfer/\", egg_pos_sku_transfer.purchase_sku_transfer, name=\"egg_pos_sku_transfer\"),\n    path(\"egg-pos/purchases/<int:purchase_id>/sku-transfer/<int:transfer_id>/reverse/\", egg_pos_sku_transfer.reverse_purchase_sku_transfer, name=\"egg_pos_reverse_sku_transfer\"),\n]\n",
        "api/views/egg_pos.py": patch_view,
        "api/templates/api/egg_pos_purchase_edit.html": patch_template,
    }
    required_new = [
        "api/models/egg_pos_sku_transfer.py", "api/services/egg_pos_sku_transfer.py",
        "api/views/egg_pos_sku_transfer.py", "api/templates/api/egg_pos_sku_transfer.html",
        "api/migrations/0100_egg_pos_sku_transfer.py",
    ]
    for fn in required_new:
        if not (ROOT / fn).exists():
            raise RuntimeError(f"Missing patch file {fn}; extract the full ZIP to the Django project root.")
    planned = {}
    for fn, transform in files.items():
        path = ROOT / fn
        if not path.is_file():
            raise RuntimeError(f"Cannot find {fn}. Run this script from the extracted Django project root.")
        current = path.read_text(encoding="utf-8")
        proposed = transform(current)
        if proposed != current:
            planned[path] = proposed
    # No existing files are touched until all substitutions have succeeded.
    if not planned:
        print("All integration changes already present; no existing files modified.")
        return
    backup_dir = ROOT / f"SKU_TRANSFER_BACKUP_{datetime.now():%Y%m%d_%H%M%S}"
    for path, proposed in planned.items():
        backup = backup_dir / path.relative_to(ROOT)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    for path, proposed in planned.items():
        path.write_text(proposed, encoding="utf-8")
        print("Updated:", path.relative_to(ROOT))
    print("Backups:", backup_dir.name)
    print("Now run: python manage.py migrate")
    print("Then: python manage.py test api.sku_transfer_tests")


if __name__ == "__main__":
    main()
