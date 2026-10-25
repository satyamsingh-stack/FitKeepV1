"""
Payments views: payment recording, outstanding tracking, fee reminders.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.exceptions import ValidationError

from members.models import MemberProfile
from payments.models import Payment, FeeReminder
from memberships.models import MemberSubscription
from payments.services import get_outstanding_payments, get_payment_summary, record_payment
from gyms.services import can_manage_members, get_user_gym, get_user_role
from audit.models import AuditLog


@login_required
def payment_list(request):
    """List payments."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view payment records.')
    payments = Payment.objects.filter(member__gym=gym).order_by('-payment_date')
    member_filter = request.GET.get('member', '')
    if member_filter:
        payments = payments.filter(member__full_name__icontains=member_filter)

    context = {
        'gym': gym,
        'payments': payments,
        'member_filter': member_filter,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'payments/list.html', context)


@login_required
def payment_add(request):
    """Record a new payment."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to record payments.')

    if request.method == 'POST':
        member_id = request.POST.get('member_id', '').strip()
        amount = request.POST.get('amount')
        subscription_id = request.POST.get('subscription_id', '').strip()
        method = request.POST.get('method', 'cash')
        reference_number = request.POST.get('reference_number', '')
        notes = request.POST.get('notes', '')

        subscription = get_object_or_404(MemberSubscription, gym=gym, id=subscription_id) if subscription_id else None
        member = subscription.member if subscription else get_object_or_404(MemberProfile, gym=gym, member_id=member_id)
        if subscription and member_id and member.member_id != member_id:
            messages.error(request, 'Selected subscription does not belong to this member.')
            return redirect('payment_add')
        try:
            payment = record_payment(
                member=member,
                amount=amount,
                method=method,
                subscription=subscription,
                reference_number=reference_number,
                notes=notes,
                recorded_by=request.user,
            )
        except (ValidationError, ValueError, TypeError):
            messages.error(request, 'Please enter a valid positive amount and payment method.')
            return redirect('payment_add')

        AuditLog.log_action(
            actor=request.user,
            action='payment_recorded',
            gym=gym,
            target=payment,
        )
        messages.success(request, 'Payment recorded successfully.')
        return redirect('payment_list')

    from memberships.services import sync_subscription_statuses
    sync_subscription_statuses(gym=gym)
    members = MemberProfile.objects.filter(gym=gym, status='active').order_by('full_name')
    subscriptions = MemberSubscription.objects.filter(gym=gym, status__in=['active', 'pending']).select_related('member', 'plan').order_by('member__full_name')
    context = {
        'gym': gym,
        'members': members,
        'subscriptions': subscriptions,
        'method_choices': Payment.METHOD_CHOICES,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'payments/form.html', context)


@login_required
def outstanding_payments(request):
    """List members with outstanding payments."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view outstanding payments.')
    outstanding = get_outstanding_payments(gym)
    context = {
        'gym': gym,
        'outstanding': outstanding,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'payments/outstanding.html', context)


@login_required
def membership_fees(request):
    """Membership fee overview."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view fee reports.')
    summary = get_payment_summary(gym)
    outstanding_items = get_outstanding_payments(gym)
    from decimal import Decimal
    summary['outstanding'] = sum((item['outstanding'] for item in outstanding_items), Decimal('0.00'))
    context = {
        'gym': gym,
        'summary': summary,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'payments/fees.html', context)