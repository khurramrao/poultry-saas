RayNoor Egg POS — Sales Reporting Fix (Order Source / Acquisition / Repeat)
============================================================================
REQUIRES: Manual Pre-Bookings, POS draft conversion, Sales Source and Repeat Customer Tracking (migration 0087) already installed.
This patch does NOT require a new migration.

INSTALL: Download ZIP into Downloads, open PyCharm terminal at the project folder (beside manage.py):

Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\RayNoor_Sales_Order_Source_Acquisition_Repeat_Reports_TARGETED.zip" -DestinationPath . -Force
python INSTALL_CHANNEL_REPORTS_FIX.py
python manage.py check
python manage.py shell -c "from django.template.loader import get_template; get_template('api/egg_pos_sales.html'); print('Sales template OK')"
python manage.py test api.channel_reporting_tests

WHAT IT DOES
- Replaces only the OLD customer acquisition panel on Egg POS Sales with three compact, collapsible reports.
- Order Source Performance counts all-time non-reversed invoices, even if there is no marketing customer profile. Website sales are identified by a linked RNWEB order; completed manually confirmed pre-bookings use their verified booking platform; manual POS sales use recorded source; older unmatched invoices are Direct / Unattributed.
- Customer Acquisition tracks only identified profiles with verified name and normalized phone, preserving the earlier matching rules.
- Repeat Customer Revenue counts second+ valid invoices, credited to the ORIGINAL customer acquisition channel.
- The three reports are all-time totals, independent of the Sales listing filter and its latest-200 display limit.
- Unpaid invoices count as INVOICED VALUE, not cash received. Reversed invoices excluded.
- No changes to sale recording, web orders, paid amounts, FIFO, commission, accounting or existing database rows.

SAFETY
- The installer checks known markers and layout; stops rather than overwrite unknown source layout.
- Timestamped backups for the two edited files.
- Safe to run a second time; does not duplicate panels.
- On Railway: deploy the changed files after verifying locally; this patch has no migrations.
- New tests provided but MUST be run in your own Django project. This environment does not include Django.
