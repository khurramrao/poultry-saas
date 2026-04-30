from django.db import migrations
from django.contrib.auth.hashers import make_password


def create_admin_user(apps, schema_editor):
    User = apps.get_model("auth", "User")

    user, created = User.objects.get_or_create(username="admin")
    user.email = "admin@test.com"
    user.is_staff = True
    user.is_superuser = True
    user.password = make_password("admin123")
    user.save()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0018_remove_salerecord_total_amount"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_admin_user),
    ]