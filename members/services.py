"""
Member services: one-step onboarding (profile + login account + membership + first payment).
"""
import secrets
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction

from accounts.models import GymMembership
from memberships.models import MemberSubscription


def create_member_account(member, gym):
    """Create (or link) the member-portal login. Returns the temporary password if a new account was made."""
    UserModel = get_user_model()
    user, created = UserModel.objects.get_or_create(
        username=member.phone or member.email or member.member_id,
        defaults={
            'phone': member.phone,
            'email': member.email or None,
            'role': 'gym_member',
            'first_name': member.full_name,
        },
    )
    temporary_password = None
    if created:
        temporary_password = secrets.token_urlsafe(12)
        user.set_password(temporary_password)
        user.save()
    GymMembership.objects.get_or_create(
        user=user,
        gym=gym,
        defaults={'role': 'member', 'status': 'active' if member.status == 'active' else 'inactive'},
    )
    return temporary_password


def subscription_status_for(gym, start_date, end_date):
    """Pending if it starts in the future, expired if already over, otherwise active."""
    from attendance.services import get_gym_local_date
    today = get_gym_local_date(gym)
    if end_date < today:
        return 'expired'
    if start_date > today:
        return 'yet_to_start'
    return 'active'


def default_end_date(start_date, duration_days):
    """Last valid day of a plan that runs for `duration_days` starting on `start_date`."""
    return start_date + timedelta(days=max(int(duration_days or 1), 1) - 1)


@transaction.atomic
def onboard_member(*, gym, member, plan=None, start_date=None, end_date=None,
                   total_amount=None, discount_amount=Decimal('0'), amount_paid=Decimal('0'),
                   payment_method='cash', reference_number='', recorded_by=None):
    """
    Onboard a new member in a single atomic step.

    `member` is an unsaved MemberProfile. Everything below succeeds or nothing is saved:
    the profile, the member-portal account, the membership, and the first payment.

    Returns a dict: member, subscription (or None), payment (or None), temporary_password.
    """
    from payments.services import record_payment

    member.gym = gym
    member.save()
    temporary_password = create_member_account(member, gym)

    subscription = None
    payment = None
    if start_date and end_date and total_amount is not None:
        subscription = MemberSubscription.objects.create(
            member=member,
            plan=plan,
            gym=gym,
            start_date=start_date,
            end_date=end_date,
            total_amount=total_amount,
            discount_amount=discount_amount or Decimal('0'),
            status=subscription_status_for(gym, start_date, end_date),
        )
        if amount_paid and amount_paid > 0:
            payment = record_payment(
                member=member,
                amount=amount_paid,
                method=payment_method,
                subscription=subscription,
                reference_number=reference_number,
                notes='Collected at onboarding',
                recorded_by=recorded_by,
            )

    return {
        'member': member,
        'subscription': subscription,
        'payment': payment,
        'temporary_password': temporary_password,
    }
