"""
Payments service module: payment recording, reminders, outstanding tracking.
"""
from decimal import Decimal
from datetime import timedelta
from django.db.models import Sum, Q
from django.core.exceptions import ValidationError
from django.utils import timezone

from members.models import MemberProfile
from payments.models import Payment, FeeReminder
from memberships.models import MemberSubscription


def record_payment(member, amount, method='cash', subscription=None, notes='', reference_number='', recorded_by=None):
    """
    Record a payment for a member.
    """
    try:
        amount = Decimal(str(amount))
    except Exception as exc:
        raise ValidationError('Enter a valid payment amount.') from exc
    if amount <= 0:
        raise ValidationError('Payment amount must be greater than zero.')
    valid_methods = {choice[0] for choice in Payment.METHOD_CHOICES}
    if method not in valid_methods:
        raise ValidationError('Choose a valid payment method.')
    if subscription and (subscription.member_id != member.id or subscription.gym_id != member.gym_id):
        raise ValidationError('The selected subscription does not belong to this member and gym.')
    payment = Payment.objects.create(
        member=member,
        subscription=subscription,
        amount=amount,
        method=method,
        reference_number=reference_number,
        notes=notes,
        recorded_by=recorded_by,
        status='completed',
    )
    if subscription:
        subscription.amount_paid = sum(
            (p.amount for p in Payment.objects.filter(subscription=subscription, status='completed')), Decimal('0.00')
        )
        subscription.save(update_fields=['amount_paid', 'updated_at'])
    return payment


def get_outstanding_payments(gym):
    """
    Get members with outstanding payments.
    """
    outstanding = []
    active_subs = MemberSubscription.objects.filter(gym=gym, status='active')
    for sub in active_subs:
        paid = Payment.objects.filter(member=sub.member, subscription=sub, status='completed').aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        outstanding_amount = sub.total_amount - sub.discount_amount - paid
        if outstanding_amount > 0:
            outstanding.append({
                'member': sub.member,
                'subscription': sub,
                'outstanding': outstanding_amount,
            })
    return outstanding


def send_fee_reminder(member, subscription, amount_due, method='in_app', sent_by=None):
    """
    Create a fee reminder for a member.
    """
    reminder = FeeReminder.objects.create(
        member=member,
        subscription=subscription,
        amount_due=amount_due,
        method=method,
        sent_by=sent_by,
    )
    return reminder


def get_payment_summary(gym, period_start=None, period_end=None):
    """
    Get payment summary for a gym.
    """
    payments = Payment.objects.filter(member__gym=gym, status='completed')
    if period_start:
        payments = payments.filter(payment_date__date__gte=period_start)
    if period_end:
        payments = payments.filter(payment_date__date__lte=period_end)

    total = payments.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
    count = payments.count()

    by_method = {}
    for method, _ in Payment.METHOD_CHOICES:
        method_total = payments.filter(method=method).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        by_method[method] = method_total

    return {
        'total': total,
        'count': count,
        'by_method': by_method,
    }