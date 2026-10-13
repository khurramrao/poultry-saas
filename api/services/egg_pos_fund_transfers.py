"""Cash/bank movements for Egg POS. A transfer is not an income or expense."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from api.models.accounting import ChartOfAccount, EggPOSFundsTransfer, JournalEntry
from api.services.accounting import ensure_default_accounts, line, money, normal_account_balance, post_entry

SOURCE_AND_DESTINATION = {
    EggPOSFundsTransfer.CASH_TO_BANK: ('1000', '1010'),
    EggPOSFundsTransfer.BANK_TO_CASH: ('1010', '1000'),
}


@transaction.atomic
def create_fund_transfer(*, user, direction, amount, reference='', notes='', request_token):
    if direction not in SOURCE_AND_DESTINATION:
        raise ValueError('Choose Main Cash to Bank or Bank to Main Cash.')
    amount = money(amount)
    if amount <= Decimal('0.00'):
        raise ValueError('Transfer amount must be greater than zero.')
    if not request_token:
        raise ValueError('The transfer form has expired. Reload and try again.')

    # Reusing the form token must never create a second transfer.
    prior = EggPOSFundsTransfer.objects.filter(request_token=request_token).first()
    if prior:
        return prior, False

    ensure_default_accounts()
    # Serialize this page's transfers against one another on PostgreSQL.
    list(ChartOfAccount.objects.select_for_update().filter(code__in=['1000', '1010']).order_by('code'))
    source_code, dest_code = SOURCE_AND_DESTINATION[direction]
    available = money(normal_account_balance(source_code))
    if amount > available:
        source_name = 'Main Cash' if source_code == '1000' else 'Bank / Digital'
        raise ValueError(f'{source_name} has only Rs {available:,.2f}. The transfer cannot exceed available funds.')

    transfer = EggPOSFundsTransfer.objects.create(
        transfer_date=timezone.localdate(), direction=direction, amount=amount,
        reference=reference[:120], notes=notes[:500], created_by=user,
        request_token=request_token,
    )
    post_entry(
        source_key=f'egg_pos:fund_transfer:{transfer.pk}',
        entry_date=transfer.transfer_date,
        reference=f'FT-{transfer.pk:06d}',
        memo=f'{transfer.get_direction_display()}' + (f' | {transfer.reference}' if transfer.reference else ''),
        source_type='fund_transfer', source_id=transfer.pk, created_by=user,
        lines=[
            line(dest_code, debit=amount, description='Internal funds transfer received'),
            line(source_code, credit=amount, description='Internal funds transfer sent'),
        ],
    )
    return transfer, True


@transaction.atomic
def void_fund_transfer(*, user, transfer_id, reason):
    reason = (reason or '').strip()
    if len(reason) < 5:
        raise ValueError('Enter a reason of at least 5 characters for voiding.')
    ensure_default_accounts()
    list(ChartOfAccount.objects.select_for_update().filter(code__in=['1000', '1010']).order_by('code'))
    transfer = EggPOSFundsTransfer.objects.select_for_update().get(pk=transfer_id)
    if transfer.is_voided:
        raise ValueError('This transfer has already been voided.')
    _, destination_code = SOURCE_AND_DESTINATION[transfer.direction]
    available = money(normal_account_balance(destination_code))
    if money(transfer.amount) > available:
        raise ValueError(
            f'Cannot void: only Rs {available:,.2f} remains in the receiving account. '
            'Move sufficient funds back first, or correct the subsequent payments.'
        )
    JournalEntry.objects.filter(source_key=f'egg_pos:fund_transfer:{transfer.pk}').delete()
    transfer.is_voided = True
    transfer.void_reason = reason[:500]
    transfer.voided_by = user
    transfer.voided_at = timezone.now()
    transfer.save(update_fields=['is_voided', 'void_reason', 'voided_by', 'voided_at'])
    return transfer
