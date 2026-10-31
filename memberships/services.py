"""
Membership service module: plan management, subscription lifecycle.
"""
from datetime import timedelta
from decimal import Decimal
from django.db.models import Q
from django.utils import timezone

from members.models import MemberProfile
from memberships.models import MembershipPlan, MemberSubscription
from payments.models import Payment


def create_subscription(member, plan, start_date, end_date, discount_amount=Decimal('0'), notes=''):
    """
    Create a new subscription for a member.
    """
    if discount_amount < 0 or discount_amount > plan.price:
        raise ValueError('Discount must be between zero and the plan price.')
    total_amount = plan.price
    subscription = MemberSubscription(
        member=member,
        plan=plan,
        gym=member.gym,
        start_date=start_date,
        end_date=end_date,
        total_amount=total_amount,
        discount_amount=discount_amount,
        notes=notes,
        status='active',
    )
    subscription.full_clean()
    subscription.save()
    return subscription


def renew_subscription(subscription, new_end_date=None, plan=None):
    """Renew without gaps: start tomorrow if still active, or today if already expired."""
    from attendance.services import get_gym_local_date
    today = get_gym_local_date(subscription.gym)
    selected_plan = plan or subscription.plan
    duration_days = selected_plan.duration_days if selected_plan else 30
    new_start_date = max(subscription.end_date + timedelta(days=1), today)
    if new_end_date is None:
        new_end_date = new_start_date + timedelta(days=duration_days - 1)
    return subscription.renew(new_end_date, plan=selected_plan, new_start_date=new_start_date)


def sync_subscription_statuses(gym=None, member=None):
    """
    Derive every membership status from its inclusive validity dates.
    """
    from attendance.services import get_gym_local_date
    if member is not None:
        gym = member.gym
    if gym is None:
        return 0
    today = get_gym_local_date(gym)
    qs = MemberSubscription.objects.filter(gym=gym)
    if member is not None:
        qs = qs.filter(member=member)
    now = timezone.now()
    changed = qs.filter(start_date__gt=today).exclude(
        status='yet_to_start',
    ).update(status='yet_to_start', updated_at=now)
    changed += qs.filter(end_date__lt=today).exclude(
        status='expired',
    ).update(status='expired', updated_at=now)
    active_memberships = qs.filter(start_date__lte=today, end_date__gte=today)
    changed += active_memberships.exclude(status='active').update(
        status='active',
        updated_at=now,
    )
    activating_member_ids = list(active_memberships.values_list('member_id', flat=True).distinct())

    if activating_member_ids:
        MemberProfile.objects.filter(
            id__in=activating_member_ids,
            status='inactive',
        ).update(status='active', updated_at=now)

    expired_members = MemberProfile.objects.filter(
        gym=gym,
        status='active',
        subscriptions__gym=gym,
        subscriptions__status='expired',
        subscriptions__end_date__lt=today,
    )
    if member is not None:
        expired_members = expired_members.filter(pk=member.pk)
    expired_member_ids = expired_members.exclude(
        subscriptions__gym=gym,
        subscriptions__status='active',
        subscriptions__start_date__lte=today,
        subscriptions__end_date__gte=today,
    ).values('id')
    MemberProfile.objects.filter(id__in=expired_member_ids).update(
        status='inactive',
        updated_at=now,
    )
    return changed


def get_active_subscription(member):
    """Get the subscription valid on the member gym's local date."""
    from attendance.services import get_gym_local_date
    sync_subscription_statuses(member=member)
    today = get_gym_local_date(member.gym)
    return member.subscriptions.filter(status='active', start_date__lte=today, end_date__gte=today).order_by('-end_date').first()


def get_expiring_subscriptions(gym, days=7):
    """Get subscriptions expiring within N days."""
    today = timezone.now().date()
    future = today + timedelta(days=days)
    return MemberSubscription.objects.filter(
        gym=gym,
        status='active',
        end_date__gte=today,
        end_date__lte=future,
    ).order_by('end_date')


def get_expired_subscriptions(gym):
    """Get subscriptions that have expired."""
    today = timezone.now().date()
    return MemberSubscription.objects.filter(
        gym=gym,
        end_date__lt=today,
    ).exclude(status='expired')


def mark_expired_subscriptions():
    """Bring every gym's subscription statuses up to date (expire finished, activate started)."""
    from gyms.models import Gym
    return sum(sync_subscription_statuses(gym=gym) for gym in Gym.objects.all())


def apply_payment_to_subscription(subscription, payment):
    """
    Apply a payment to a subscription's outstanding balance.
    """
    subscription.amount_paid += payment.amount
    subscription.save()
    return subscription.outstanding_balance


def get_member_payment_status(member):
    """
    Get payment status for a member's active subscription.
    """
    subscription = get_active_subscription(member)
    if not subscription:
        return {'has_subscription': False, 'outstanding': Decimal('0')}

    payments = Payment.objects.filter(member=member, subscription=subscription)
    total_paid = sum(p.amount for p in payments if p.status == 'completed')
    outstanding = subscription.total_amount - subscription.discount_amount - total_paid

    return {
        'has_subscription': True,
        'subscription': subscription,
        'total_amount': subscription.total_amount,
        'discount_amount': subscription.discount_amount,
        'total_paid': total_paid,
        'outstanding': max(outstanding, Decimal('0')),
    }


def renew_with_payment(subscription, plan=None, total_amount=None, discount_amount=Decimal('0'),
                       amount_paid=Decimal('0'), payment_method='cash', reference_number='',
                       start_date=None, end_date=None, recorded_by=None):
    """
    Renew a membership and (optionally) collect the payment in the same step.

    Dates default to the usual no-gap rule: start the day after the current one ends
    (or today if it already ended) and run for the plan's duration.
    """
    from django.db import transaction
    from attendance.services import get_gym_local_date
    from payments.services import record_payment

    discount_amount = discount_amount or Decimal('0')
    amount_paid = amount_paid or Decimal('0')
    selected_plan = plan or subscription.plan
    today = get_gym_local_date(subscription.gym)
    start_date = start_date or max(subscription.end_date + timedelta(days=1), today)
    if end_date is None:
        days = selected_plan.duration_days if selected_plan else 30
        end_date = start_date + timedelta(days=days - 1)
    if total_amount is None:
        total_amount = selected_plan.price if selected_plan else subscription.total_amount
    if end_date < start_date:
        raise ValueError('End date must be on or after the start date.')
    if discount_amount < 0 or discount_amount > total_amount:
        raise ValueError('Discount must be between zero and the fee.')
    if amount_paid < 0 or amount_paid > total_amount - discount_amount:
        raise ValueError('Amount paid cannot exceed the amount due.')

    with transaction.atomic():
        new_sub = subscription.renew(end_date, plan=selected_plan, total_amount=total_amount, new_start_date=start_date)
        if discount_amount:
            new_sub.discount_amount = discount_amount
            new_sub.save(update_fields=['discount_amount', 'updated_at'])
        payment = None
        if amount_paid > 0:
            payment = record_payment(
                member=new_sub.member, amount=amount_paid, method=payment_method,
                subscription=new_sub, reference_number=reference_number,
                notes='Collected at renewal', recorded_by=recorded_by,
            )
        if new_sub.status == 'active' and new_sub.member.status == 'inactive':
            new_sub.member.status = 'active'
            new_sub.member.save(update_fields=['status', 'updated_at'])
    return new_sub, payment
