"""Review and (optionally) reconcile three IDENTICAL copies of a goat investor account.

Default is read-only. Example:
    python manage.py reconcile_duplicate_goat_accounts --name "Muhammad Husnain Ali"

Only after verifying the three user IDs, the true goat codes, payment evidence,
backing up the DB, and confirming the intended canonical owner:
    python manage.py reconcile_duplicate_goat_accounts \
      --name "Muhammad Husnain Ali" --keep-owner-id 123 --apply \
      --confirm THREE-GOATS-113000

The apply mode is intentionally conservative. It will STOP if records differ,
any duplicate goat has related activity, or the amount is not exactly confirmed.
"""
import base64
from collections import Counter
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from api.models.goats import Goat, GoatAccountPayment
from api.views.goats import _owner_goat_account_snapshot

EXPECTED_GOATS = 3
EXPECTED_COST = Decimal("113000.00")
EXPECTED_PAID = Decimal("113000.00")
CONFIRMATION = "THREE-GOATS-113000"


def _normalise(name):
    return " ".join((name or "").split()).casefold()


def _label(user):
    return user.get_full_name().strip() or user.username


def _goat_identity(goat):
    # Ignore generated row IDs, goat codes, creator and timestamps only.
    # Identical costs alone NEVER prove two goats are duplicates.
    return (
        (goat.name or "").strip().casefold(),
        (goat.breed or "").strip().casefold(),
        goat.sex, goat.status, goat.acquisition_type,
        str(goat.purchase_date), str(goat.date_of_birth),
        bool(goat.date_of_birth_is_estimated),
        str(goat.purchase_cost), str(goat.purchase_weight_kg),
        goat.shed_id, (goat.sire_external or "").strip().casefold(),
        (goat.dam_external or "").strip().casefold(),
        goat.sire_id, goat.dam_id,
        (goat.notes or "").strip(),
    )


def _payment_identity(payment):
    return (
        str(payment.payment_date), str(payment.amount),
        payment.payment_method, (payment.reference or "").strip(),
        (payment.notes or "").strip(),
    )


def _safe_json_value(value):
    if isinstance(value, (bytes, memoryview, bytearray)):
        return {"binary_base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _record(model_instance):
    return {
        field.attname: _safe_json_value(getattr(model_instance, field.attname))
        for field in model_instance._meta.concrete_fields
    }


def _related_goat_activity(goat):
    """Any FK pointing to a goat blocks deletion; no cascading historical data."""
    counts = []
    for rel in goat._meta.related_objects:
        if not getattr(rel, "field", None):
            continue
        if getattr(rel, "one_to_one", False) or getattr(rel, "one_to_many", False):
            model = rel.related_model
            fk_name = rel.field.name
            count = model._default_manager.filter(**{fk_name: goat}).count()
            if count:
                counts.append((model._meta.label, fk_name, count))
    return counts


class Command(BaseCommand):
    help = "Audit three same-name goat investor accounts; reconcile only exact confirmed duplicates."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Investor's displayed full name")
        parser.add_argument("--keep-owner-id", type=int, help="Exact User ID of the genuine account")
        parser.add_argument("--apply", action="store_true", help="Delete confirmed duplicate records")
        parser.add_argument("--confirm", default="", help="Required confirmation for --apply")

    def _matched_owners(self, name, lock=False):
        User = get_user_model()
        # Do not combine SELECT FOR UPDATE with DISTINCT: PostgreSQL rejects it.
        owner_ids = Goat.objects.order_by().values("owner_id").distinct()
        qs = User.objects.filter(pk__in=owner_ids).order_by("id")
        if lock:
            qs = qs.select_for_update()
        return [u for u in qs if _normalise(_label(u)) == _normalise(name)]

    def _owner_data(self, owner, lock=False):
        qs = Goat.objects.filter(owner=owner).order_by("id")
        ps = GoatAccountPayment.objects.filter(owner=owner).order_by("id")
        if lock:
            qs = qs.select_for_update()
            ps = ps.select_for_update()
        goats, payments = list(qs), list(ps)
        snapshot = _owner_goat_account_snapshot(owner, "all")
        return {"owner": owner, "goats": goats, "payments": payments, "snap": snapshot}

    def _print_audit(self, groups):
        self.stdout.write("\nGOAT ACCOUNTS AUDIT — READ ONLY UNTIL --apply")
        for g in groups:
            u, s = g["owner"], g["snap"]
            self.stdout.write(
                f"User ID {u.pk}; username={u.username!r}; name={_label(u)!r}; "
                f"goats={len(g['goats'])}; account_cost=Rs {s['total_cost']}; "
                f"contributions=Rs {s['total_paid']}"
            )
            for goat in g["goats"]:
                self.stdout.write(
                    f"  GOAT id={goat.pk} code={goat.goat_code} "
                    f"name={goat.name!r} breed={goat.breed!r} sex={goat.sex} "
                    f"cost={goat.purchase_cost} weight={goat.purchase_weight_kg} "
                    f"status={goat.status}"
                )
            for p in g["payments"]:
                self.stdout.write(
                    f"  PAYMENT id={p.pk} date={p.payment_date} "
                    f"amount=Rs {p.amount} method={p.payment_method} "
                    f"reference={p.reference!r}"
                )
        self.stdout.write("Correct target: 1 genuine investor account, 3 goats, Rs 113,000 invested.")

    def _validate(self, groups, keep_owner_id):
        if len(groups) != 3:
            raise CommandError(
                f"STOP: Found {len(groups)} same-name active owner accounts, expected 3. "
                "Do not guess which records to correct."
            )
        kept = next((g for g in groups if g["owner"].pk == keep_owner_id), None)
        if kept is None:
            raise CommandError("STOP: The supplied --keep-owner-id is not one of these accounts.")
        if (len(kept["goats"]) != EXPECTED_GOATS
                or kept["snap"]["total_cost"] != EXPECTED_COST
                or kept["snap"]["total_paid"] != EXPECTED_PAID):
            raise CommandError("STOP: Kept account is not exactly 3 goats / Rs 113,000 cost / Rs 113,000 paid.")
        expected_goats = Counter(_goat_identity(g) for g in kept["goats"])
        expected_payments = Counter(_payment_identity(p) for p in kept["payments"])
        if not expected_payments:
            raise CommandError("STOP: Kept account has no payment records to match.")
        duplicates = [g for g in groups if g is not kept]
        for candidate in duplicates:
            s = candidate["snap"]
            if (len(candidate["goats"]) != EXPECTED_GOATS
                    or s["total_cost"] != EXPECTED_COST
                    or s["total_paid"] != EXPECTED_PAID):
                raise CommandError("STOP: A suspected duplicate has different goat counts or money amounts.")
            if Counter(_goat_identity(goat) for goat in candidate["goats"]) != expected_goats:
                raise CommandError(
                    f"STOP: User ID {candidate['owner'].pk} has different goat details. "
                    "Do not delete actual goats based on matching names or totals."
                )
            if Counter(_payment_identity(p) for p in candidate["payments"]) != expected_payments:
                raise CommandError(
                    f"STOP: User ID {candidate['owner'].pk} has different payments. "
                    "Confirm genuine payment receipts before reconciliation."
                )
            for goat in candidate["goats"]:
                activity = _related_goat_activity(goat)
                if activity:
                    raise CommandError(
                        f"STOP: Goat {goat.goat_code} has related records {activity}; "
                        "manual audited consolidation is required."
                    )
        return kept, duplicates

    def handle(self, *args, **options):
        name = (options["name"] or "").strip()
        if not name:
            raise CommandError("Name is required.")
        apply = options["apply"]
        keep_id = options["keep_owner_id"]
        if apply and (keep_id is None or options["confirm"] != CONFIRMATION):
            raise CommandError(
                "STOP: --apply requires --keep-owner-id and "
                f"--confirm {CONFIRMATION}. Verify original goat codes and payment records first."
            )
        if not apply:
            groups = [self._owner_data(u) for u in self._matched_owners(name)]
            self._print_audit(groups)
            self.stdout.write("NO RECORDS CHANGED. Choose the genuine owner ID only after checking evidence.")
            return

        # Recheck everything with row locks, then archive before data correction.
        with transaction.atomic():
            groups = [self._owner_data(u, lock=True) for u in self._matched_owners(name, lock=True)]
            self._print_audit(groups)
            kept, duplicates = self._validate(groups, keep_id)
            backup_dir = Path.cwd() / "raynoor_goat_reconciliation_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = backup_dir / f"goat_reconcile_before_{stamp}.json"
            archive = {
                "purpose": "Reconciliation audit, NOT a full database backup",
                "correct_investor_name": name,
                "kept_user_id": kept["owner"].pk,
                "expected_goats": EXPECTED_GOATS,
                "expected_paid": str(EXPECTED_PAID),
                "accounts": [
                    {
                        "user": {
                            "id": g["owner"].pk,
                            "username": g["owner"].username,
                            "name": _label(g["owner"]),
                        },
                        "goats": [_record(o) for o in g["goats"]],
                        "payments": [_record(p) for p in g["payments"]],
                    }
                    for g in groups
                ],
            }
            path.write_text(json.dumps(archive, indent=2, default=str), encoding="utf-8")
            # Preserve the chosen account; remove *only* identical duplicate goat
            # rows and their duplicate payment records, leaving user logins intact.
            # We explicitly forbid any goat with relational history above.
            deleted_goats = deleted_payments = 0
            for g in duplicates:
                ids = [goat.pk for goat in g["goats"]]
                pids = [p.pk for p in g["payments"]]
                if pids:
                    deleted_payments += GoatAccountPayment.objects.filter(pk__in=pids).delete()[0]
                if ids:
                    deleted_goats += Goat.objects.filter(pk__in=ids).delete()[0]

            # Model-level postcondition within transaction; any failure rolls back.
            kept_now = self._owner_data(kept["owner"])
            if (len(kept_now["goats"]) != 3
                    or kept_now["snap"]["total_cost"] != EXPECTED_COST
                    or kept_now["snap"]["total_paid"] != EXPECTED_PAID):
                raise CommandError("STOP: Post-correction verification failed; DB transaction rolled back.")
            if any(Goat.objects.filter(owner=g["owner"]).exists() for g in duplicates):
                raise CommandError("STOP: Duplicate goats remain; DB transaction rolled back.")
            self.stdout.write(self.style.SUCCESS(
                f"CORRECTED: kept owner ID {keep_id}; "
                f"removed {deleted_goats} duplicate goat rows and {deleted_payments} "
                f"duplicate payment rows. Exact audit file: {path}"
            ))
            self.stdout.write("Other user accounts remain intact. Recheck Goat Farm, Goat Accounts and payment ledger.")
