"""PostgreSQL regression: voiding must not lock a nullable user join."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from api.models.egg_pos import EggPOSPurchase, EggPOSSupplier
from api.services.egg_pos_purchase_void import void_supplier_purchase


class PurchaseVoidPostgresRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="void_pg_test_admin", email="voidpg@example.test", password="check_password_123"
        )
        cls.supplier = EggPOSSupplier.objects.create(name="Nullable user join supplier")

    def test_lock_does_not_join_nullable_created_by_user(self):
        purchase = EggPOSPurchase.objects.create(supplier=self.supplier, created_by=None)
        with CaptureQueriesContext(connection) as queries:
            # No items => business validation error after the purchase is locked.
            # The old PostgreSQL query raised DatabaseError before this point.
            with self.assertRaisesMessage(ValueError, "no item records"):
                void_supplier_purchase(
                    purchase_id=purchase.pk, actor=self.admin,
                    reason="Duplicate created by mistake",
                )
        purchase_selects = [q["sql"] for q in queries if
                            "api_eggpospurchase" in q["sql"] and "SELECT" in q["sql"].upper()]
        self.assertTrue(purchase_selects)
        self.assertTrue(all('JOIN "auth_user"' not in q for q in purchase_selects))
        purchase.refresh_from_db()
        self.assertFalse(purchase.is_voided)
