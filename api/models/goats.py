from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from api.models.sensor import Shed


ZERO = Decimal("0.00")


class Goat(models.Model):
    SEX_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
    ]

    STATUS_CHOICES = [
        ("active", "Active"),
        ("sold", "Sold"),
        ("deceased", "Deceased"),
    ]

    ACQUISITION_CHOICES = [
        ("purchased", "Purchased"),
        ("born_on_farm", "Born on Farm"),
    ]

    goat_code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        editable=False,
    )

    name = models.CharField(max_length=100, blank=True)
    breed = models.CharField(max_length=100, blank=True)

    sex = models.CharField(
        max_length=10,
        choices=SEX_CHOICES,
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_goats",
    )

    # Pedigree. Registered parents are preferred because they allow the
    # relationship checker to trace multiple generations. External text is
    # available for purchased goats whose parents are known but not in RayNoor.
    sire = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="sired_offspring",
        null=True,
        blank=True,
        limit_choices_to={"sex": "male"},
    )
    dam = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="dam_offspring",
        null=True,
        blank=True,
        limit_choices_to={"sex": "female"},
    )
    sire_external = models.CharField(
        max_length=120,
        blank=True,
        help_text="Known sire identity when the sire is not registered in RayNoor.",
    )
    dam_external = models.CharField(
        max_length=120,
        blank=True,
        help_text="Known dam identity when the dam is not registered in RayNoor.",
    )

    # Real farm Shed relation. The old label is retained for backward
    # compatibility with already-created goat rows and old templates.
    shed = models.ForeignKey(
        Shed,
        on_delete=models.PROTECT,
        related_name="goats",
        null=True,
        blank=True,
        limit_choices_to={"shed_type": "goat"},
    )

    shed_label = models.CharField(
        max_length=80,
        default="Shed 3",
        help_text="Kept separate from poultry Shed records for now.",
    )

    acquisition_type = models.CharField(
        max_length=20,
        choices=ACQUISITION_CHOICES,
        default="purchased",
    )

    purchase_date = models.DateField(default=timezone.localdate)
    date_of_birth = models.DateField(blank=True, null=True)
    date_of_birth_is_estimated = models.BooleanField(
        default=False,
        help_text="Tick when the date of birth is estimated rather than known exactly.",
    )

    purchase_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )

    purchase_weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_goats",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["goat_code", "id"]
        verbose_name = "Goat"
        verbose_name_plural = "Goats"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if self.shed:
            self.shed_label = self.shed.name
        super().save(*args, **kwargs)

        if is_new and not self.goat_code:
            self.goat_code = f"RN-{self.pk:04d}"
            super().save(update_fields=["goat_code", "updated_at"])

    @property
    def display_name(self):
        if self.name:
            return f"{self.goat_code} · {self.name}"
        return self.goat_code

    @property
    def owner_display_name(self):
        full_name = self.owner.get_full_name().strip()
        return full_name or self.owner.username

    @property
    def location_name(self):
        if self.shed_id:
            return self.shed.name
        return self.shed_label or "Shed 3"

    @property
    def current_weight_kg(self):
        latest = self.weight_records.order_by(
            "-record_date",
            "-id",
        ).first()

        if latest is not None:
            return latest.weight_kg

        return self.purchase_weight_kg

    @property
    def sire_display(self):
        if self.sire_id:
            return self.sire.display_name
        return self.sire_external or "Unknown"

    @property
    def dam_display(self):
        if self.dam_id:
            return self.dam.display_name
        return self.dam_external or "Unknown"

    def clean(self):
        errors = {}
        if self.sire_id:
            if self.pk and self.sire_id == self.pk:
                errors["sire"] = "A goat cannot be its own sire."
            elif self.sire.sex != "male":
                errors["sire"] = "Sire must be a male goat."
            elif self.pk and self.pk in goat_ancestor_map(self.sire, max_generations=8):
                errors["sire"] = "This sire is a descendant of the goat and would create a pedigree cycle."
        if self.dam_id:
            if self.pk and self.dam_id == self.pk:
                errors["dam"] = "A goat cannot be its own dam."
            elif self.dam.sex != "female":
                errors["dam"] = "Dam must be a female goat."
            elif self.pk and self.pk in goat_ancestor_map(self.dam, max_generations=8):
                errors["dam"] = "This dam is a descendant of the goat and would create a pedigree cycle."
        if self.sire_id and self.dam_id and self.sire_id == self.dam_id:
            errors["dam"] = "Sire and dam cannot be the same goat."
        today = timezone.localdate()
        if self.purchase_date and self.purchase_date > today:
            errors["purchase_date"] = "Acquisition date cannot be in the future."
        if self.date_of_birth and self.date_of_birth > today:
            errors["date_of_birth"] = "Date of birth cannot be in the future."
        if self.date_of_birth and self.purchase_date and self.date_of_birth > self.purchase_date:
            errors["date_of_birth"] = "Date of birth cannot be after acquisition."
        if self.date_of_birth_is_estimated and not self.date_of_birth:
            errors["date_of_birth"] = "Enter an estimated date of birth or untick Estimated."
        if self.acquisition_type == "born_on_farm":
            if not self.date_of_birth:
                errors["date_of_birth"] = "A farm-born goat requires a date of birth."
            elif self.purchase_date and self.date_of_birth != self.purchase_date:
                errors["purchase_date"] = "For a farm-born goat, entry date must match birth date."
            if self.date_of_birth_is_estimated:
                errors["date_of_birth_is_estimated"] = "A recorded farm birth must use its actual birth date."
        if self.date_of_birth and self.sire_id and self.sire.date_of_birth:
            if self.sire.date_of_birth >= self.date_of_birth:
                errors["date_of_birth"] = "The goat must be born after its recorded father."
        if self.date_of_birth and self.dam_id and self.dam.date_of_birth:
            if self.dam.date_of_birth >= self.date_of_birth:
                errors["date_of_birth"] = "The goat must be born after its recorded mother."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.display_name} - {self.owner_display_name}"


def _normalise_parent_text(value):
    return " ".join((value or "").strip().upper().split())


def _parent_keys(goat):
    keys = []
    if goat.sire_id:
        keys.append(("parent", _normalise_parent_text(goat.sire.goat_code)))
    elif goat.sire_external:
        keys.append(("parent", _normalise_parent_text(goat.sire_external)))

    if goat.dam_id:
        keys.append(("parent", _normalise_parent_text(goat.dam.goat_code)))
    elif goat.dam_external:
        keys.append(("parent", _normalise_parent_text(goat.dam_external)))
    return [key for key in keys if key[1]]


def _external_parent_matches(text, goat):
    value = _normalise_parent_text(text)
    if not value or goat is None:
        return False
    candidates = {
        _normalise_parent_text(goat.goat_code),
        _normalise_parent_text(goat.display_name),
    }
    if goat.name:
        candidates.add(_normalise_parent_text(goat.name))
    return value in candidates


def goat_ancestor_map(goat, max_generations=4):
    """Return registered ancestors as {goat_id: minimum_generation}."""
    result = {}
    queue = []
    if goat.sire_id:
        queue.append((goat.sire, 1))
    if goat.dam_id:
        queue.append((goat.dam, 1))

    while queue:
        ancestor, generation = queue.pop(0)
        if ancestor is None or generation > max_generations:
            continue
        previous = result.get(ancestor.id)
        if previous is not None and previous <= generation:
            continue
        result[ancestor.id] = generation
        if generation < max_generations:
            if ancestor.sire_id:
                queue.append((ancestor.sire, generation + 1))
            if ancestor.dam_id:
                queue.append((ancestor.dam, generation + 1))
    return result


def _pedigree_complete(goat, depth=3, visited=None):
    """Conservative completeness check for a green compatibility result."""
    if depth <= 0:
        return True
    visited = set(visited or set())
    if goat.id in visited:
        return False
    visited.add(goat.id)
    if not goat.sire_id or not goat.dam_id:
        return False
    return _pedigree_complete(goat.sire, depth - 1, visited) and _pedigree_complete(goat.dam, depth - 1, visited)


def check_goat_relationship(doe, buck, max_generations=4):
    """
    Classify mating risk from recorded pedigree.

    Hard blocks: parent/child, full or half siblings, and any direct
    ancestor/descendant found within the checked pedigree. Warnings cover
    aunt/uncle relationships, first cousins, and other close common ancestry.
    An incomplete pedigree is UNKNOWN rather than incorrectly marked safe.
    """
    result = {
        "risk": "unknown",
        "label": "Relationship Unknown",
        "details": "Pedigree information is incomplete. Confirm family history before mating.",
        "common_ancestors": [],
    }

    if doe is None or buck is None:
        return result
    if doe.id == buck.id:
        return {
            **result,
            "risk": "blocked",
            "label": "Same Goat — Blocked",
            "details": "A goat cannot be paired with itself.",
        }
    if doe.sex != "female" or buck.sex != "male":
        return {
            **result,
            "risk": "blocked",
            "label": "Invalid Sex Pairing",
            "details": "Select a female doe and a male buck.",
        }

    # Parent-child relationships.
    if doe.sire_id == buck.id or _external_parent_matches(doe.sire_external, buck):
        return {**result, "risk": "blocked", "label": "Father × Daughter — Blocked", "details": f"{buck.goat_code} is recorded as the sire of {doe.goat_code}."}
    if buck.dam_id == doe.id or _external_parent_matches(buck.dam_external, doe):
        return {**result, "risk": "blocked", "label": "Mother × Son — Blocked", "details": f"{doe.goat_code} is recorded as the dam of {buck.goat_code}."}

    # Full/half sibling check including external first-generation identities.
    doe_keys = set(_parent_keys(doe))
    buck_keys = set(_parent_keys(buck))
    shared_parent_keys = doe_keys & buck_keys
    if shared_parent_keys:
        # Both known parent identities match => full siblings. One => half siblings.
        if len(shared_parent_keys) >= 2 and len(doe_keys) >= 2 and len(buck_keys) >= 2:
            return {**result, "risk": "blocked", "label": "Full Brother × Sister — Blocked", "details": "The selected goats share both recorded parents."}
        return {**result, "risk": "blocked", "label": "Half Brother × Sister — Blocked", "details": "The selected goats share a recorded sire or dam."}

    doe_ancestors = goat_ancestor_map(doe, max_generations=max_generations)
    buck_ancestors = goat_ancestor_map(buck, max_generations=max_generations)

    # Any direct ancestor/descendant relationship found in the checked depth.
    if buck.id in doe_ancestors:
        generation = doe_ancestors[buck.id]
        label = "Grandfather × Granddaughter — Blocked" if generation == 2 else "Direct Male Ancestor × Descendant — Blocked"
        return {**result, "risk": "blocked", "label": label, "details": f"{buck.goat_code} is a generation-{generation} ancestor of {doe.goat_code}."}
    if doe.id in buck_ancestors:
        generation = buck_ancestors[doe.id]
        label = "Grandmother × Grandson — Blocked" if generation == 2 else "Direct Female Ancestor × Descendant — Blocked"
        return {**result, "risk": "blocked", "label": label, "details": f"{doe.goat_code} is a generation-{generation} ancestor of {buck.goat_code}."}

    common_ids = set(doe_ancestors) & set(buck_ancestors)
    if common_ids:
        common = sorted(
            ((ancestor_id, doe_ancestors[ancestor_id], buck_ancestors[ancestor_id]) for ancestor_id in common_ids),
            key=lambda item: (item[1] + item[2], max(item[1], item[2]), item[0]),
        )
        ancestor_id, doe_gen, buck_gen = common[0]
        ancestor = Goat.objects.filter(pk=ancestor_id).first()
        ancestor_name = ancestor.display_name if ancestor else f"Goat #{ancestor_id}"

        if sorted((doe_gen, buck_gen)) == [1, 2]:
            label = "Aunt/Uncle × Niece/Nephew — Warning"
        elif doe_gen == 2 and buck_gen == 2:
            label = "First Cousins — Warning"
        else:
            label = "Close Common Ancestor — Warning"

        return {
            **result,
            "risk": "warning",
            "label": label,
            "details": f"Both goats descend from {ancestor_name}. Relationship distances: {doe_gen} and {buck_gen} generations.",
            "common_ancestors": [item[0] for item in common],
        }

    if _pedigree_complete(doe, depth=3) and _pedigree_complete(buck, depth=3):
        return {
            **result,
            "risk": "safe",
            "label": "Suitable — No Close Relationship Found",
            "details": "No shared or direct ancestor was found within three fully recorded generations.",
        }

    return result


class GoatBreedingRecord(models.Model):
    STATUS_CHOICES = [
        ("mated", "Mated"),
        ("pregnant", "Pregnant"),
        ("kidded", "Kidded"),
        ("failed", "Not Pregnant / Failed"),
        ("cancelled", "Cancelled"),
    ]

    RISK_CHOICES = [
        ("safe", "Suitable"),
        ("warning", "Warning"),
        ("unknown", "Unknown"),
    ]

    doe = models.ForeignKey(
        Goat,
        on_delete=models.PROTECT,
        related_name="breeding_records_as_doe",
        limit_choices_to={"sex": "female"},
    )
    buck = models.ForeignKey(
        Goat,
        on_delete=models.PROTECT,
        related_name="breeding_records_as_buck",
        limit_choices_to={"sex": "male"},
    )
    mating_date = models.DateField(default=timezone.localdate)
    expected_kidding_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="mated")
    relationship_risk = models.CharField(max_length=20, choices=RISK_CHOICES, default="unknown")
    relationship_label = models.CharField(max_length=180, blank=True)
    relationship_details = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_goat_breeding_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-mating_date", "-id"]
        verbose_name = "Goat Breeding Record"
        verbose_name_plural = "Goat Breeding Records"

    def clean(self):
        errors = {}
        if self.doe_id and self.doe.sex != "female":
            errors["doe"] = "Doe must be female."
        if self.buck_id and self.buck.sex != "male":
            errors["buck"] = "Buck must be male."
        if self.doe_id and self.buck_id:
            check = check_goat_relationship(self.doe, self.buck)
            if check["risk"] == "blocked":
                errors["buck"] = check["label"]
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.doe_id and self.buck_id:
            check = check_goat_relationship(self.doe, self.buck)
            if check["risk"] == "blocked":
                raise ValidationError({"buck": check["label"]})
            self.relationship_risk = check["risk"]
            self.relationship_label = check["label"]
            self.relationship_details = check["details"]
        if self.mating_date and not self.expected_kidding_date:
            from datetime import timedelta
            self.expected_kidding_date = self.mating_date + timedelta(days=150)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.doe.goat_code} × {self.buck.goat_code} - {self.mating_date}"


class GoatKiddingRecord(models.Model):
    breeding_record = models.OneToOneField(
        GoatBreedingRecord,
        on_delete=models.PROTECT,
        related_name="kidding_record",
    )
    kidding_date = models.DateField(default=timezone.localdate)
    kids = models.ManyToManyField(
        Goat,
        related_name="kidding_events",
        blank=True,
    )
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_kiddings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-kidding_date", "-id"]
        verbose_name = "Goat Kidding Record"
        verbose_name_plural = "Goat Kidding Records"

    @property
    def kid_count(self):
        return self.kids.count()

    def __str__(self):
        return f"Kidding {self.breeding_record.doe.goat_code} - {self.kidding_date}"


class GoatWeightRecord(models.Model):
    goat = models.ForeignKey(
        Goat,
        on_delete=models.CASCADE,
        related_name="weight_records",
    )

    record_date = models.DateField(default=timezone.localdate)

    weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_weights",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-id"]
        verbose_name = "Goat Weight Record"
        verbose_name_plural = "Goat Weight Records"

    def __str__(self):
        return f"{self.goat.goat_code} - {self.weight_kg} kg - {self.record_date}"


class GoatCostEntry(models.Model):
    CATEGORY_CHOICES = [
        ("feed", "Feed"),
        ("medicine", "Medicine"),
        ("expense", "Expense"),
    ]

    ALLOCATION_SCOPE_CHOICES = [
        ("individual", "One Goat"),
        ("selected", "Selected Goats"),
        ("all_active", "All Active Goats"),
    ]

    MEDICINE_TYPE_CHOICES = [
        ("medicine", "Medicine"),
        ("vaccine", "Vaccine"),
        ("multivitamin", "Multivitamin"),
    ]

    EXPENSE_CATEGORY_CHOICES = [
        ("diesel", "Diesel"),
        ("labor", "Labor"),
        ("electricity", "Electricity"),
        ("transport", "Transport"),
        ("maintenance", "Maintenance"),
        ("rent", "Farm Rent"),
        ("internet", "Internet"),
        ("service", "Service Charges"),
        ("misc", "Misc"),
    ]

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    shed = models.ForeignKey(
        Shed,
        on_delete=models.PROTECT,
        related_name="goat_cost_entries",
        limit_choices_to={"shed_type": "goat"},
    )
    entry_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    title = models.CharField(max_length=140, blank=True)
    medicine_type = models.CharField(
        max_length=20,
        choices=MEDICINE_TYPE_CHOICES,
        blank=True,
    )
    expense_category = models.CharField(
        max_length=20,
        choices=EXPENSE_CATEGORY_CHOICES,
        blank=True,
    )
    allocation_scope = models.CharField(
        max_length=20,
        choices=ALLOCATION_SCOPE_CHOICES,
        default="all_active",
    )
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_goat_cost_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        verbose_name = "Goat Cost Entry"
        verbose_name_plural = "Goat Cost Entries"

    @property
    def display_title(self):
        if self.title:
            return self.title
        if self.category == "feed":
            return "Goat Feed"
        if self.category == "medicine":
            return self.get_medicine_type_display() or "Medicine"
        if self.category == "expense":
            return self.get_expense_category_display() or "Expense"
        return self.get_category_display()

    def __str__(self):
        return f"{self.get_category_display()} - Rs {self.amount} - {self.entry_date}"


class GoatCostAllocation(models.Model):
    cost_entry = models.ForeignKey(
        GoatCostEntry,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    goat = models.ForeignKey(
        Goat,
        on_delete=models.CASCADE,
        related_name="cost_allocations",
    )
    owner_snapshot = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="goat_cost_allocations",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cost_entry__entry_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cost_entry", "goat"],
                name="unique_goat_cost_allocation",
            )
        ]

    def __str__(self):
        return f"{self.goat.goat_code} - {self.cost_entry.get_category_display()} - Rs {self.amount}"


class GoatSale(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("cheque", "Cheque"),
        ("credit", "Credit"),
        ("other", "Other"),
    ]

    goat = models.OneToOneField(
        Goat,
        on_delete=models.PROTECT,
        related_name="sale_record",
    )
    sale_date = models.DateField(default=timezone.localdate)
    buyer_name = models.CharField(max_length=140, blank=True)
    sale_weight_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    rate_per_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    locked_total_cost = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="cash",
    )
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_sales",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sale_date", "-id"]
        verbose_name = "Goat Sale"
        verbose_name_plural = "Goat Sales"

    @property
    def profit(self):
        return (self.total_amount or ZERO) - (self.locked_total_cost or ZERO)

    @property
    def roi(self):
        if not self.locked_total_cost:
            return ZERO
        return (self.profit / self.locked_total_cost) * Decimal("100")

    def __str__(self):
        return f"{self.goat.goat_code} sale - Rs {self.total_amount}"


class GoatAccountPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank Transfer"),
        ("cheque", "Cheque"),
        ("other", "Other"),
    ]

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="goat_account_payments",
    )
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="bank_transfer",
    )
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_goat_account_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "Goat Account Payment"
        verbose_name_plural = "Goat Account Payments"

    def __str__(self):
        owner_name = self.owner.get_full_name().strip() or self.owner.username
        return f"{owner_name} - Rs {self.amount}"
