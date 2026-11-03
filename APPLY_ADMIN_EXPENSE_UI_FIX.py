#!/usr/bin/env python3
"""RayNoor Egg POS Admin Expenses UI + Chrome autofill targeted fix.

Changes ONLY api/templates/api/egg_pos_admin_expenses.html; safe to run twice.
No models, accounting, views, migrations, or database writes.
"""
from pathlib import Path
from datetime import datetime
import hashlib
import os
import shutil
import tempfile

EXPECTED_ORIGINAL_SHA256 = 'c7715e73f62e8b8704105a28baff3c50a2ab1b44f5bb10eb1ecf53fc27927ac0'
PATCHED_SHA256 = 'f55ede02e3edb841ef521eeb1888bec09f3546ccccebc7ac2ca1803f864969a9'
NEW_TEMPLATE = r'''{% extends "api/egg_pos_base.html" %}{% load humanize %}
{% block egg_pos_css %}
<style>
/* Scoped to Admin Expenses only. Does not change the shared Egg POS layout. */
.ep-adm-shell {max-width:1180px;margin:0 auto;color:#17345a}
.ep-adm-intro {margin-bottom:16px}
.ep-adm-intro .ep-section-title {margin-bottom:0}
.ep-adm-shell .ep-kpis {margin-bottom:16px}
.ep-adm-shell .ep-panel {border:1px solid #d9e5f2;box-shadow:0 3px 16px rgba(33,68,112,.035);border-radius:16px;overflow:hidden;background:#fff}
.ep-adm-shell .ep-panel-head {display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:14px;font-weight:800}
.ep-adm-shell .ep-panel-body {padding:20px}
.ep-adm-meta {display:block;font-weight:500;color:#71839e;font-size:11px;margin-top:4px}
.ep-adm-note {display:flex;align-items:flex-start;gap:12px;padding:13px 16px;margin:0 0 18px;background:#f2f7fe;border:1px solid #d5e7fb;border-radius:12px;font-size:12px;line-height:1.6;color:#365675}
.ep-adm-note i {color:#1f75d3;font-size:17px;margin-top:2px}
.ep-adm-form-section {margin:2px 0 13px;padding-bottom:8px;border-bottom:1px solid #e8eef5;display:flex;align-items:baseline;gap:10px}
.ep-adm-form-section strong {font-size:12px;color:#143d6b}
.ep-adm-form-section small {font-size:11px;color:#8292a6}
.ep-adm-fields {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 18px}
.ep-adm-shell .ep-field {min-width:0}
.ep-adm-shell .ep-field label {display:block;color:#345475;font-size:11px;font-weight:800;letter-spacing:.025em;text-transform:uppercase;margin:0 0 7px}
.ep-adm-shell .ep-input,.ep-adm-shell .ep-select,.ep-adm-shell .ep-textarea,.ep-adm-void input {width:100%;box-sizing:border-box;border:1px solid #cbdbee;border-radius:10px;background:#fff;color:#17345a;min-height:43px;padding:10px 12px;font-size:13px;transition:border-color .15s,box-shadow .15s}
.ep-adm-shell .ep-textarea {display:block;resize:vertical;min-height:80px}
.ep-adm-shell .ep-input:focus,.ep-adm-shell .ep-select:focus,.ep-adm-shell .ep-textarea:focus,.ep-adm-void input:focus {outline:none;border-color:#2275d4;box-shadow:0 0 0 3px rgba(34,117,212,.13)}
.ep-adm-shell .ep-input::placeholder,.ep-adm-void input::placeholder {color:#91a1b5}
.ep-adm-wide {grid-column:1/-1}
.ep-adm-security {display:flex;align-items:flex-end;gap:16px;justify-content:space-between;background:#f7faff;border:1px solid #dfebf8;padding:14px;border-radius:12px;margin-top:18px}
.ep-adm-security .ep-field {flex:1;max-width:360px}
.ep-adm-security-hint {font-size:11px;color:#72839b;margin-top:6px}
.ep-adm-submit {white-space:nowrap;min-height:42px;padding:10px 19px}
.ep-adm-username-hint {position:absolute!important;left:-9999px!important;top:auto!important;width:1px!important;height:1px!important;opacity:0!important;pointer-events:none!important;overflow:hidden!important}
.ep-adm-history {overflow-x:auto}
.ep-adm-history .ep-table {width:100%;border-collapse:collapse;min-width:830px;font-size:12px}
.ep-adm-history .ep-table th {white-space:nowrap;text-transform:uppercase;font-size:10px;letter-spacing:.04em;color:#69809d;background:#f4f8fc;padding:13px 12px}
.ep-adm-history .ep-table td {padding:14px 12px;vertical-align:middle;border-top:1px solid #edf1f6;color:#25415e}
.ep-adm-history .ep-table tbody tr:hover {background:#fbfdff}
.ep-adm-history .ep-table small {font-size:11px;color:#7c8fa7;line-height:1.45}
.ep-adm-history .ep-table .ep-adm-money {white-space:nowrap;color:#17345a;font-weight:800}
.ep-adm-badge {display:inline-flex;padding:5px 9px;align-items:center;border-radius:99px;background:#e7f8ef;color:#15834b;font-size:10px;font-weight:800}
.ep-adm-badge.void {background:#ffeded;color:#b02c38}
.ep-adm-correct {margin-top:9px;min-width:170px}
.ep-adm-correct>summary {cursor:pointer;display:inline-flex;align-items:center;gap:7px;border:1px solid #e6cad0;color:#a33844;border-radius:8px;padding:7px 11px;font-weight:700;font-size:11px;list-style:none;user-select:none}
.ep-adm-correct>summary::-webkit-details-marker {display:none}
.ep-adm-correct>summary:focus-visible {outline:2px solid #287bd1;outline-offset:2px}
.ep-adm-correct[open]>summary {background:#fff2f3}
.ep-adm-void {display:grid;gap:9px;min-width:220px;padding:12px;border:1px solid #f0d8da;background:#fff9f9;border-radius:10px;margin-top:9px}
.ep-adm-void label {display:block;font-size:10px;color:#8b4851;font-weight:800;text-transform:uppercase;margin-bottom:5px}
.ep-adm-void input {font-size:12px;min-height:38px;padding:8px 10px}
.ep-adm-void .ep-btn {justify-self:start}
.ep-adm-void-help {font-size:11px;color:#9b6770;line-height:1.4}
@media(max-width:800px){.ep-adm-shell .ep-panel-body{padding:15px}.ep-adm-security{flex-direction:column;align-items:stretch}.ep-adm-security .ep-field{max-width:none}.ep-adm-submit{width:100%;white-space:normal}}
@media(max-width:580px){.ep-adm-fields{grid-template-columns:1fr}.ep-adm-wide{grid-column:auto}.ep-adm-form-section{display:block}.ep-adm-form-section small{display:block;margin-top:3px}}
</style>
{% endblock %}
{% block egg_pos_content %}
<div class="ep-adm-shell">
  <div class="ep-adm-intro">
    <div class="ep-section-title">
      <div>
        <h3><i class="fa-solid fa-building-circle-check"></i> Admin &amp; Office Expenses</h3>
        <p>Record shop rent, stationery, utilities, repairs and equipment—separately from staff selling costs.</p>
      </div>
      <a class="ep-btn" href="{% url 'egg_pos_expenses' %}"><i class="fa-solid fa-gas-pump"></i> Fuel / Staff Expenses</a>
    </div>
  </div>

  <section class="ep-kpis" aria-label="Expense summary">
    <div class="ep-kpi orange"><div class="ep-kpi-label">General Operating Costs</div><strong>Rs {{ operating_total|floatformat:0|intcomma }}</strong><div class="ep-kpi-note">All dates · included in profit and loss</div></div>
    <div class="ep-kpi green"><div class="ep-kpi-label">Equipment / Fixed Assets</div><strong>Rs {{ equipment_total|floatformat:0|intcomma }}</strong><div class="ep-kpi-note">All dates · capitalized as assets</div></div>
  </section>

  <div class="ep-adm-note" role="note">
    <i class="fa-solid fa-circle-info" aria-hidden="true"></i>
    <div><strong>How this affects accounts:</strong> Payment comes from Main Cash or Bank. Ordinary expenses affect company profit; equipment is treated as a fixed asset. Neither changes sales commission, staff collections or supplier payable. Use Farm accounts for separate farm costs.</div>
  </div>

  <section class="ep-panel" aria-labelledby="ae-title">
    <div class="ep-panel-head" id="ae-title">
      <div>Record an Expense or Asset Purchase<span class="ep-adm-meta">Required fields are marked with *</span></div>
      <i class="fa-solid fa-file-circle-plus" aria-hidden="true"></i>
    </div>
    <div class="ep-panel-body">
      <form method="post" autocomplete="off" id="ep-admin-expense-create">
        {% csrf_token %}
        <input type="hidden" name="request_token" value="{{ request_token }}">
        <div class="ep-adm-form-section"><strong>Expense details</strong><small>What was purchased and when?</small></div>
        <div class="ep-adm-fields">
          <div class="ep-field"><label for="ae-date">Date *</label><input id="ae-date" class="ep-input" type="date" name="expense_date" value="{{ today|date:'Y-m-d' }}" required></div>
          <div class="ep-field"><label for="ae-category">Expense Category *</label><select id="ae-category" class="ep-select" name="category" required>{% for code,label in categories %}<option value="{{ code }}">{{ label }}</option>{% endfor %}</select></div>
          <div class="ep-field"><label for="ae-amount">Amount (Rs) *</label><input id="ae-amount" class="ep-input" type="number" min="0.01" max="999999999999.99" step="0.01" name="amount" inputmode="decimal" placeholder="0.00" required></div>
          <div class="ep-field"><label for="ae-payment">Paid From *</label><select id="ae-payment" class="ep-select" name="payment_source" required>{% for code,label in payment_sources %}<option value="{{ code }}">{{ label }}</option>{% endfor %}</select></div>
          <div class="ep-field"><label for="ae-payee">Paid To</label><input id="ae-payee" class="ep-input" type="text" name="paid_to" maxlength="160" placeholder="e.g., Landlord, office supplier" autocomplete="off"></div>
          <div class="ep-field"><label for="ae-reference">Receipt / Reference</label><input id="ae-reference" class="ep-input" type="text" name="reference" maxlength="120" placeholder="Optional invoice, voucher or receipt number" autocomplete="off" autocorrect="off" autocapitalize="off" data-lpignore="true" data-1p-ignore="true"></div>
          <div class="ep-field ep-adm-wide"><label for="ae-notes">Description</label><textarea id="ae-notes" class="ep-textarea" name="notes" rows="2" placeholder="e.g., November office rent or printer purchased for the POS" autocomplete="off"></textarea></div>
        </div>
        <div class="ep-adm-security">
          <div class="ep-field">
            <label for="ae-auth"><i class="fa-solid fa-lock" aria-hidden="true"></i> Confirm Admin Password *</label>
            <div class="ep-adm-username-hint" aria-hidden="true"><label for="ae-login-hint">Account</label><input id="ae-login-hint" type="text" name="login_username_hint" value="{{ request.user.username }}" autocomplete="username" readonly tabindex="-1"></div>
            <input id="ae-auth" class="ep-input" type="password" name="admin_password" autocomplete="new-password" data-lpignore="true" data-1p-ignore="true" placeholder="Enter your admin password" required>
            <div class="ep-adm-security-hint">Your password confirms this payment. It is not part of the receipt reference.</div>
          </div>
          <button class="ep-btn primary ep-adm-submit" type="submit"><i class="fa-solid fa-check-circle"></i> Save &amp; Post Expense</button>
        </div>
      </form>
    </div>
  </section>

  <section class="ep-panel" style="margin-top:18px" aria-labelledby="ae-history-title">
    <div class="ep-panel-head" id="ae-history-title">
      <div>Expense History<span class="ep-adm-meta">Latest 200 entries · voids retain a complete audit trail</span></div>
      <i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i>
    </div>
    <div class="ep-adm-history">
      <table class="ep-table"><thead><tr><th>Date</th><th>Category / Detail</th><th>Paid To</th><th>Amount</th><th>Source</th><th>Recorded By</th><th>Status / Action</th></tr></thead>
      <tbody>{% for e in rows %}
        <tr>
          <td>{{ e.expense_date|date:'d M Y' }}</td>
          <td><strong>{{ e.get_category_display }}</strong>{% if e.notes %}<br><small>{{ e.notes|truncatechars:85 }}</small>{% endif %}{% if e.reference %}<br><small>Ref: {{ e.reference }}</small>{% endif %}</td>
          <td>{{ e.paid_to|default:'—' }}</td>
          <td><span class="ep-adm-money">Rs {{ e.amount|floatformat:2|intcomma }}</span>{% if e.is_capital_asset %}<br><small>Fixed asset</small>{% endif %}</td>
          <td>{{ e.get_payment_source_display }}</td>
          <td>{{ e.recorded_by.username }}</td>
          <td>{% if e.is_voided %}<span class="ep-adm-badge void">Voided</span><br><small>{{ e.void_reason }}</small>{% else %}
            <span class="ep-adm-badge">Posted</span>
            <details class="ep-adm-correct">
              <summary><i class="fa-solid fa-rotate-left" aria-hidden="true"></i> Void entry</summary>
              <form method="post" class="ep-adm-void" action="{% url 'egg_pos_void_admin_expense' e.id %}" autocomplete="off">
                {% csrf_token %}
                <div><label for="ae-void-reason-{{ e.id }}">Reason for reversal *</label><input id="ae-void-reason-{{ e.id }}" type="text" name="reason" maxlength="500" minlength="12" placeholder="e.g., Duplicate expense entered" required autocomplete="off"></div>
                <div><label for="ae-void-password-{{ e.id }}">Admin password *</label><input id="ae-void-password-{{ e.id }}" type="password" name="admin_password" placeholder="Confirm password" required autocomplete="new-password" data-lpignore="true" data-1p-ignore="true"></div>
                <div class="ep-adm-void-help">This posts an audited reversal, not a deletion.</div>
                <button type="submit" class="ep-btn danger" onclick="return confirm('Void admin expense #{{ e.id }} and post an audited reversal?');">Confirm void</button>
              </form>
            </details>
          {% endif %}</td>
        </tr>
      {% empty %}<tr><td colspan="7">No admin expenses recorded yet.</td></tr>{% endfor %}</tbody></table>
    </div>
  </section>
</div>
{% endblock %}
'''


def normalized(contents):
    return contents.lstrip("\ufeff").replace("\r\n", "\n")


def digest(contents):
    return hashlib.sha256(normalized(contents).encode("utf-8")).hexdigest()


def main():
    home = Path(__file__).resolve().parent
    target = home / "api" / "templates" / "api" / "egg_pos_admin_expenses.html"
    if not (home / "manage.py").is_file():
        raise SystemExit("STOP: Put this file in the poultrysaas folder, beside manage.py.")
    if not target.exists():
        raise SystemExit("STOP: Admin Expenses template was not found. Install Admin Expenses before applying this UI fix.")
    original = target.read_text(encoding="utf-8-sig")
    current_hash = digest(original)
    if current_hash == PATCHED_SHA256:
        print("Already installed. No changes made.")
        return
    if current_hash != EXPECTED_ORIGINAL_SHA256:
        raise SystemExit(
            "STOP: Your Admin Expenses template differs from the known version. "
            "No files were changed. Do not overwrite it manually; share the current template "
            "so the UI patch can be merged safely. File: " + str(target)
        )
    backup = target.with_name(target.name + ".before_admin_ui_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak")
    shutil.copy2(target, backup)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",newline="\n",dir=target.parent,
                                         prefix=".admin_exp_ui_",suffix=".tmp",delete=False) as f:
            tmp_name=f.name
            f.write(NEW_TEMPLATE)
        if digest(Path(tmp_name).read_text(encoding="utf-8")) != PATCHED_SHA256:
            raise RuntimeError("Template validation failed; no changes applied.")
        os.replace(tmp_name,target)
        print("Updated: api/templates/api/egg_pos_admin_expenses.html")
        print("Backup: " + backup.name)
        print("No migrations required. Run: python manage.py check")
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


if __name__ == "__main__":
    main()
