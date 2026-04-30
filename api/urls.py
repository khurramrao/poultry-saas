from django.urls import path
from api.views import sensor, camera, sales


urlpatterns = [
    path("sensor-data/", sensor.receive_sensor_data),
    path("dashboard/", sensor.dashboard, name="dashboard"),
    path("vaccines/<int:batch_id>/", sensor.vaccine_records, name="vaccine_records"),
    path("vaccines/done/<int:record_id>/", sensor.mark_vaccine_done, name="mark_vaccine_done"),
    path("batch/<int:batch_id>/close/", sales.close_batch, name="close_batch"),
    path("batch/<int:batch_id>/sales/", sales.sale_records, name="sale_records"),
    path("meat-sales/", sales.meat_sales_summary, name="meat_sales_summary"),
    path("meat-sales/<int:batch_id>/", sales.meat_sale_detail, name="meat_sale_detail"),

]