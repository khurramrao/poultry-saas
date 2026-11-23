"""Install RayNoor Supplier Purchase Returns without overwriting newer application files.

Run from the folder containing manage.py:
    python APPLY_RAYNOOR_SUPPLIER_RETURNS.py
"""
from pathlib import Path
from datetime import datetime
import ast
import shutil
import sys

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'api'
PKG = ROOT / 'RAYNOOR_SUPPLIER_RETURNS_PATCH_FILES'
MARK = '# RAYNOOR_SUPPLIER_RETURNS_V1'


def fail(message):
    raise SystemExit('INSTALL STOPPED (no files changed): ' + message)


def replace_one(text, old, new, name):
    if new in text:
        return text
    if old not in text:
        fail(name + ': expected insertion point not found. Project code differs; merge manually.')
    return text.replace(old, new, 1)


def find_migration_leaf():
    folder = APP / 'migrations'
    items = {}
    for p in folder.glob('[0-9]*_*.py'):
        if 'supplier_purchase_returns' in p.name:
            return None, p.name
        tree = ast.parse(p.read_text(encoding='utf-8-sig'))
        deps = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == 'Migration':
                for statement in node.body:
                    if isinstance(statement, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='dependencies' for t in statement.targets):
                        if isinstance(statement.value,(ast.List,ast.Tuple)):
                            for element in statement.value.elts:
                                if isinstance(element,ast.Tuple) and len(element.elts)>=2:
                                    try:
                                        app=ast.literal_eval(element.elts[0]); dep=ast.literal_eval(element.elts[1])
                                    except (ValueError,TypeError):continue
                                    if app=='api':deps.append(dep)
        items[p.stem] = set(deps)
    if not items:
        fail('No existing api migrations were found.')
    referenced = set().union(*items.values())
    leaves = sorted(set(items)-referenced)
    if len(leaves)!=1:
        fail('Multiple migration leaves remain: '+', '.join(leaves)+'. Merge them before installing this patch.')
    next_number = max(int(stem.split('_')[0]) for stem in items)+1
    name = f'{next_number:04d}_supplier_purchase_returns.py'
    return leaves[0], name


def main():
    if not (ROOT/'manage.py').exists() or not (APP/'urls.py').exists():
        fail('Extract this ZIP beside manage.py, preserving RAYNOOR_SUPPLIER_RETURNS_PATCH_FILES folder.')
    if not PKG.exists():
        fail('Patch source folder not found beside this installer.')

    # Build and validate every change before touching the user's project.
    updates = {}
    models_init = APP / 'models' / '__init__.py'
    t = models_init.read_text(encoding='utf-8')
    imp = 'from .egg_pos_supplier_returns import EggPOSSupplierReturn, EggPOSSupplierReturnRefund'
    if imp not in t:
        t = t.rstrip() + '\n\n' + MARK + '\n' + imp + '\n'
    updates[models_init] = t

    accounts = APP/'services'/'accounting.py'
    t = accounts.read_text(encoding='utf-8')
    if '"1160": ("Supplier Returns Refund Receivable"' not in t:
        t = replace_one(t, 'ACCOUNT_SPECS = {', '''ACCOUNT_SPECS = {
    # RayNoor supplier returns: approved refunds and unrecovered landed costs.
    "1160": ("Supplier Returns Refund Receivable", "asset", "debit"),
    "4100": ("Supplier Return Recovery Difference", "revenue", "credit"),
    "6160": ("Unrecovered Supplier Return Cost", "expense", "debit"),''', str(accounts))
    updates[accounts] = t

    urls = APP/'urls.py'
    t = urls.read_text(encoding='utf-8')
    if 'from api.views import egg_pos_supplier_returns' not in t:
        t = replace_one(t, 'urlpatterns = [', 'from api.views import egg_pos_supplier_returns\n\nurlpatterns = [', str(urls))
    route = '''    path("egg-pos/purchases/returns/", egg_pos_supplier_returns.purchase_returns, name="egg_pos_purchase_returns"),'''
    if route not in t:
        t = replace_one(t, 'urlpatterns = [', 'urlpatterns = [\n'+route, str(urls))
    updates[urls] = t

    inventory = APP/'templates'/'api'/'egg_pos_inventory.html'
    t = inventory.read_text(encoding='utf-8')
    url_tag = "{% url 'egg_pos_purchase_returns' %}"
    if url_tag not in t:
        needle = 'Correct Purchase</a>'
        insertion = '''Correct Purchase</a> <a class="ep-btn" href="{% url 'egg_pos_purchase_returns' %}?lot={{ lot.id }}" title="Return this exact lot to its original supplier">Return to Supplier</a>'''
        t = replace_one(t, needle, insertion, str(inventory))
        # Standalone shortcut in the inventory header, optional if layout differs.
        needle2 = 'Purchase History</a>'
        t = replace_one(t, needle2, 'Purchase History</a> <a class="ep-btn" href="{% url \'egg_pos_purchase_returns\' %}"><i class="fa-solid fa-rotate-left"></i> Supplier Returns</a>', str(inventory))
    updates[inventory] = t

    supplier_page = APP/'templates'/'api'/'egg_pos_supplier_detail.html'
    t=supplier_page.read_text(encoding='utf-8')
    if "{% url 'egg_pos_purchase_returns' %}" not in t:
        t=replace_one(t, 'All Suppliers</a>', 'All Suppliers</a> <a class="ep-btn" href="{% url \'egg_pos_purchase_returns\' %}">Supplier Returns</a>', str(supplier_page))
    updates[supplier_page]=t

    purchase_view = APP/'views'/'egg_pos.py'
    t=purchase_view.read_text(encoding='utf-8')
    start=t.find('def edit_purchase(')
    if start==-1:fail('Cannot locate edit_purchase to protect already returned purchase values.')
    end=t.find('\n@login_required', start+5)
    if end==-1:fail('Cannot find the end of edit_purchase.')
    chunk=t[start:end]
    guard = '''    # Prevent cost/supplier edits after a recorded return without revaluation.
    if request.method == "POST" and purchase.supplier_returns.filter(is_voided=False).exists():
        messages.error(request, "This purchase has active supplier returns. Reconcile/reverse returns before editing purchase costs or supplier.")
        return redirect("egg_pos_purchase_returns")

'''
    if 'purchase.supplier_returns.filter(is_voided=False).exists()' not in chunk:
        needle='    if request.method == "POST":\n        try:'
        if needle not in chunk:fail('Cannot insert purchase correction safety check; form layout differs.')
        chunk=chunk.replace(needle, guard+needle,1)
        t=t[:start]+chunk+t[end:]
    updates[purchase_view]=t

    migration_parent, migration_name = find_migration_leaf()
    new_files = {
        APP/'models'/'egg_pos_supplier_returns.py': PKG/'api'/'models'/'egg_pos_supplier_returns.py',
        APP/'services'/'egg_pos_supplier_returns.py': PKG/'api'/'services'/'egg_pos_supplier_returns.py',
        APP/'views'/'egg_pos_supplier_returns.py': PKG/'api'/'views'/'egg_pos_supplier_returns.py',
        APP/'templates'/'api'/'egg_pos_purchase_returns.html': PKG/'api'/'templates'/'api'/'egg_pos_purchase_returns.html',
        APP/'supplier_purchase_returns_tests.py': PKG/'api'/'supplier_purchase_returns_tests.py',
    }
    for path, source in new_files.items():
        if not source.is_file():fail('Patch file missing: '+str(source))
        if path.exists() and path.read_bytes()!=source.read_bytes():
            fail('File already exists with different contents; refusing to overwrite: '+str(path))
    if migration_parent is not None:
        migration_path=APP/'migrations'/migration_name
        deps=f'[("api", "{migration_parent}")]'
        migration_src=(PKG/'MIGRATION_TEMPLATE.py.txt').read_text(encoding='utf-8').replace('__DEPENDENCIES__',deps)
        compile(migration_src,str(migration_path),'exec')
        new_files[migration_path]=None
    else:
        migration_path=APP/'migrations'/migration_name
        print('Using existing return migration:', migration_name)

    for path, content in updates.items():
        compile(content, str(path), 'exec') if path.suffix == '.py' else None
    backups=ROOT/'raynoor_supplier_return_backups'/datetime.now().strftime('%Y%m%d_%H%M%S')
    backups.mkdir(parents=True,exist_ok=True)
    for path,content in updates.items():
        if path.read_text(encoding='utf-8')!=content:
            original=backups/path.relative_to(ROOT)
            original.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,original)
            path.write_text(content,encoding='utf-8')
            print('Updated:',path.relative_to(ROOT))
    for path,source in new_files.items():
        if path.exists():continue
        path.parent.mkdir(parents=True,exist_ok=True)
        if source is None:
            path.write_text(migration_src,encoding='utf-8')
        else:
            shutil.copy2(source,path)
        print('Added:',path.relative_to(ROOT))
    print('\nBackups:',backups.relative_to(ROOT))
    print('Check: python manage.py check')
    print('Check: python manage.py makemigrations --check --dry-run')
    print('Check: python manage.py migrate --plan && python manage.py migrate')
    print('Test : python manage.py test api.supplier_purchase_returns_tests')
    print('Then open /api/egg-pos/purchases/returns/ and review on a staging database.')

if __name__=='__main__':
    main()
