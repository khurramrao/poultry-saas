from decimal import Decimal
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

def backfill(apps,schema_editor):
    Sale=apps.get_model("api","EggPOSSale"); Payment=apps.get_model("api","EggPOSSalePayment")
    for s in Sale.objects.all().iterator():
        if s.payment_method != "credit" and s.net_total and s.net_total > Decimal("0.00"):
            Payment.objects.create(sale_id=s.id,payment_date=s.sale_date,amount=s.net_total,payment_method=s.payment_method if s.payment_method in {"cash","bank_transfer","other"} else "other",notes="Backfilled pre-credit sale",recorded_by_id=s.created_by_id)

class Migration(migrations.Migration):
    dependencies=[("api","0053_egg_pos_v1"),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.AddField(model_name="eggpossale",name="customer_phone",field=models.CharField(blank=True,max_length=40)),migrations.AddField(model_name="eggpossale",name="credit_due_date",field=models.DateField(blank=True,null=True)),migrations.CreateModel(name="EggPOSSalePayment",fields=[("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("payment_date",models.DateField(default=django.utils.timezone.localdate)),("amount",models.DecimalField(decimal_places=2,max_digits=14)),("payment_method",models.CharField(choices=[("cash","Cash"),("bank_transfer","Bank Transfer"),("easypaisa","Easypaisa"),("jazzcash","JazzCash"),("other","Other")],default="cash",max_length=30)),("reference",models.CharField(blank=True,max_length=120)),("notes",models.TextField(blank=True)),("created_at",models.DateTimeField(auto_now_add=True)),("recorded_by",models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name="egg_pos_sale_payments_recorded",to=settings.AUTH_USER_MODEL)),("sale",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="payments",to="api.eggpossale"))],options={"ordering":["payment_date","id"]}),migrations.RunPython(backfill,migrations.RunPython.noop)]
