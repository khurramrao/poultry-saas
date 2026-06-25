from django.urls import path
from api.views import sensor, camera, sales, logs, finance_tracker


urlpatterns = [
    path("sensor-data/", sensor.receive_sensor_data),
    path("dashboard/", sensor.dashboard, name="dashboard"),
    path("vaccines/<int:batch_id>/", sensor.vaccine_records, name="vaccine_records"),
    path("vaccines/done/<int:record_id>/", sensor.mark_vaccine_done, name="mark_vaccine_done"),
    path("batch/<int:batch_id>/close/", sales.close_batch, name="close_batch"),
    path("batch/<int:batch_id>/sales/", sales.sale_records, name="sale_records"),
    path("meat-sales/", sales.meat_sales_summary, name="meat_sales_summary"),
    path("meat-sales/<int:batch_id>/", sales.meat_sale_detail, name="meat_sale_detail"),
    path("daily-log/", logs.daily_log, name="daily_log"),
    path("finance-tracker/", finance_tracker.finance_tracker, name="finance_tracker"),
    path("add-sale/", finance_tracker.add_sale_record, name="add_sale_record"),
    path("feed-list/", finance_tracker.feed_list, name="feed_list"),
    path("add-feed/", finance_tracker.add_feed_entry, name="add_feed_entry"),
    path("medicine-list/", finance_tracker.medicine_list, name="medicine_list"),
    path("add-medicine/",finance_tracker.add_medicine_entry, name="add_medicine_entry"),
    path("expenses/", sales.expense_list, name="expense_list"),
    path("add-expense/",sales.add_expense,name="add_expense"),
    path("add-chick-cost/", finance_tracker.add_chick_cost, name="add_chick_cost"),



]