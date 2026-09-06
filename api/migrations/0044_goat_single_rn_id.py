from django.db import migrations


def unify_goat_ids(apps, schema_editor):
    Goat = apps.get_model("api", "Goat")
    goats = list(Goat.objects.all().order_by("id"))

    if not goats:
        return

    # Build one permanent RN identifier per goat. Existing RN ear tags
    # are preserved where possible so physical tags already in use stay valid.
    assigned = {}
    used_codes = set()

    for goat in goats:
        tag = (goat.tag_number or "").strip().upper()
        if tag.startswith("RN-") and tag not in used_codes:
            assigned[goat.id] = tag
            used_codes.add(tag)

    next_number = 1
    for goat in goats:
        if goat.id in assigned:
            continue

        while True:
            candidate = f"RN-{next_number:04d}"
            next_number += 1
            if candidate not in used_codes:
                break

        assigned[goat.id] = candidate
        used_codes.add(candidate)

    # Avoid unique collisions while converting G-xxxx to RN-xxxx.
    for goat in goats:
        Goat.objects.filter(pk=goat.pk).update(
            goat_code=f"TMP-{goat.pk}"
        )

    for goat in goats:
        Goat.objects.filter(pk=goat.pk).update(
            goat_code=assigned[goat.id],
            tag_number=assigned[goat.id],
        )


def reverse_goat_ids(apps, schema_editor):
    Goat = apps.get_model("api", "Goat")
    goats = list(Goat.objects.all().order_by("id"))

    for goat in goats:
        Goat.objects.filter(pk=goat.pk).update(
            goat_code=f"TMP-{goat.pk}"
        )

    for goat in goats:
        old_code = f"G-{goat.pk:04d}"
        Goat.objects.filter(pk=goat.pk).update(
            goat_code=old_code,
            tag_number=goat.goat_code,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0043_goat_goatweightrecord"),
    ]

    operations = [
        migrations.RunPython(
            unify_goat_ids,
            reverse_goat_ids,
        ),
        migrations.RemoveField(
            model_name="goat",
            name="tag_number",
        ),
    ]
