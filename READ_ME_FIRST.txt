RAYNOOR — ADMIN EXPENSES PAGE UI / CHROME AUTOFILL FIX
====================================================
This ZIP contains ONE installer script. No folder copying needed.

Extract it into your existing PyCharm project folder (beside manage.py), then run:

    python APPLY_ADMIN_EXPENSE_UI_FIX.py
    python manage.py check

Only api/templates/api/egg_pos_admin_expenses.html changes. It makes a .bak backup and refuses to overwrite a customized template. No migration or database changes.

Changes: clearer form layout, mobile responsiveness, less cluttered expense history, expandable audited Void form, and HTML autocomplete hints to reduce Chrome using Receipt/Reference as a login username.

Browsers and password managers can override autocomplete hints. If Chrome still suggests saving the admin password, choose Never/No Thanks, not Save. The fix does not alter previously recorded references. Use existing audited corrections for any historical mistakes.
