"""
Membership views: plans, subscriptions, renewals.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta

from memberships.models import MembershipPlan, MemberSubscription
from memberships.forms import MembershipPlanForm, SubscriptionEditForm, SubscriptionForm, SubscriptionRenewalForm
from memberships.services import renew_subscription, get_expiring_subscriptions
from members.models import MemberProfile
from gyms.services import can_manage_members, get_user_gym, get_user_role
from audit.models import AuditLog


@login_required
def plan_list(request):
    """List membership plans."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view membership plans.')
    plans = MembershipPlan.objects.filter(gym=gym).order_by('name')
    context = {
        'gym': gym,
        'plans': plans,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'memberships/plans.html', context)


@login_required
def plan_add(request):
    """Add a new membership plan."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to add plans.')

    if request.method == 'POST':
        form = MembershipPlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.gym = gym
            plan.save()

            AuditLog.log_action(
                actor=request.user,
                action='create',
                gym=gym,
                target=plan,
            )
            messages.success(request, 'Plan created successfully.')
            return redirect('plan_list')
    else:
        form = MembershipPlanForm()

    context = {
        'gym': gym,
        'form': form,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'memberships/plan_form.html', context)


@login_required
def plan_edit(request, plan_id):
    """Edit a membership plan (price, duration, active/inactive)."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to edit plans.')

    plan = get_object_or_404(MembershipPlan, id=plan_id, gym=gym)
    if request.method == 'POST':
        form = MembershipPlanForm(request.POST, instance=plan)
        if form.is_valid():
            form.save()
            AuditLog.log_action(actor=request.user, action='update', gym=gym, target=plan)
            messages.success(request, 'Plan updated. Existing memberships keep the price they were sold at.')
            return redirect('plan_list')
    else:
        form = MembershipPlanForm(instance=plan)

    context = {
        'gym': gym,
        'form': form,
        'plan': plan,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'memberships/plan_form.html', context)


@login_required
def subscription_list(request):
    """List subscriptions."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view subscriptions.')
    from attendance.services import get_gym_local_date
    from memberships.services import sync_subscription_statuses
    from django.db.models import Q
    sync_subscription_statuses(gym=gym)
    today = get_gym_local_date(gym)

    subscriptions = MemberSubscription.objects.filter(gym=gym).select_related('member', 'plan').order_by('-start_date')
    status_filter = request.GET.get('status', '')
    quick = request.GET.get('filter', '')
    search = request.GET.get('search', '').strip()
    if status_filter:
        subscriptions = subscriptions.filter(status=status_filter)
    if search:
        subscriptions = subscriptions.filter(
            Q(member__full_name__icontains=search) | Q(member__phone__icontains=search) | Q(member__member_id__icontains=search)
        )
    if quick == 'expiring':
        subscriptions = subscriptions.filter(status='active', end_date__gte=today, end_date__lte=today + timedelta(days=7)).order_by('end_date')

    rows = list(subscriptions)
    if quick == 'due':
        rows = [sub for sub in rows if sub.status in ('active', 'yet_to_start') and sub.outstanding_balance > 0]
    for sub in rows:
        sub.balance = sub.outstanding_balance
        sub.days_left = (sub.end_date - today).days

    context = {
        'gym': gym,
        'subscriptions': rows,
        'status_filter': status_filter,
        'quick_filter': quick,
        'search': search,
        'status_choices': MemberSubscription.STATUS_CHOICES,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'memberships/subscriptions.html', context)


@login_required
def subscription_add(request):
    """Add a new subscription."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to add subscriptions.')

    if request.method == 'POST':
        form = SubscriptionForm(request.POST, gym=gym)
        if form.is_valid():
            member = form.cleaned_data['member']
            plan = form.cleaned_data['plan']
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            discount = form.cleaned_data.get('discount_amount', 0)
            total_amount = form.cleaned_data['total_amount']
            from attendance.services import get_gym_local_date
            today = get_gym_local_date(gym)
            status = 'yet_to_start' if start_date > today else ('expired' if end_date < today else 'active')
            try:
                with transaction.atomic():
                    member = MemberProfile.objects.select_for_update().get(pk=member.pk, gym=gym)
                    subscription = MemberSubscription(
                        member=member,
                        plan=plan,
                        gym=gym,
                        start_date=start_date,
                        end_date=end_date,
                        total_amount=total_amount,
                        discount_amount=discount,
                        status=status,
                    )
                    subscription.full_clean()
                    subscription.save()
            except ValidationError as exc:
                form.add_error('start_date', exc)
            except IntegrityError:
                form.add_error(None, 'Another membership already exists for this member and date range.')
            else:
                AuditLog.log_action(
                    actor=request.user,
                    action='create',
                    gym=gym,
                    target=subscription,
                )
                messages.success(request, 'Subscription created successfully.')
                return redirect('subscription_list')
    else:
        initial = {}
        member_pk = request.GET.get('member', '')
        if member_pk.isdigit():
            preselected = MemberProfile.objects.filter(gym=gym, pk=member_pk).first()
            if preselected:
                initial['member'] = preselected
        from attendance.services import get_gym_local_date
        initial['start_date'] = get_gym_local_date(gym)
        form = SubscriptionForm(gym=gym, initial=initial)

    plans_json = [
        {'id': p.id, 'price': str(p.price), 'days': p.duration_days}
        for p in MembershipPlan.objects.filter(gym=gym, is_active=True)
    ]
    context = {
        'gym': gym,
        'form': form,
        'plans_json': plans_json,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'memberships/subscription_form.html', context)


@login_required
def subscription_edit(request, subscription_id):
    """Edit a membership's plan, dates, fee, status, or notes."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to edit memberships.')

    subscription = get_object_or_404(
        MemberSubscription.objects.select_related('member', 'plan'),
        id=subscription_id,
        gym=gym,
    )
    if request.method == 'POST':
        form = SubscriptionEditForm(request.POST, instance=subscription, gym=gym)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_subscription = form.save()
                    from memberships.services import sync_subscription_statuses
                    sync_subscription_statuses(member=updated_subscription.member)
            except IntegrityError:
                form.add_error(None, 'Another membership already exists for this member and date range.')
            else:
                AuditLog.log_action(
                    actor=request.user,
                    action='update',
                    gym=gym,
                    target=updated_subscription,
                    metadata={'updated_fields': 'membership'},
                )
                messages.success(request, 'Membership updated successfully.')
                return redirect('subscription_list')
    else:
        form = SubscriptionEditForm(instance=subscription, gym=gym)

    return render(request, 'memberships/subscription_edit.html', {
        'gym': gym,
        'subscription': subscription,
        'form': form,
        'user_role': get_user_role(request.user, gym),
    })


@login_required
def subscription_renew(request, subscription_id):
    """Renew a subscription."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to renew subscriptions.')

    subscription = get_object_or_404(MemberSubscription, id=subscription_id, gym=gym)
    from attendance.services import get_gym_local_date
    from memberships.services import renew_with_payment
    from payments.models import Payment

    today = get_gym_local_date(gym)
    new_start = max(subscription.end_date + timedelta(days=1), today)
    plan = subscription.plan
    duration_days = plan.duration_days if plan else 30
    default_fee = subscription.total_amount
    initial = {
        'plan': plan,
        'start_date': new_start,
        'end_date': new_start + timedelta(days=duration_days - 1),
        'total_amount': default_fee,
        'discount_amount': 0,
        'amount_paid': 0,
        'payment_method': 'cash',
    }

    if request.method == 'POST':
        post_data = request.POST.copy()
        post_defaults = {
            'plan': plan.pk if plan else '',
            'start_date': new_start.isoformat(),
            'end_date': (new_start + timedelta(days=duration_days - 1)).isoformat(),
            'total_amount': str(default_fee),
            'discount_amount': '0',
            'amount_paid': '0',
            'payment_method': 'cash',
            'reference_number': '',
        }
        for field, value in post_defaults.items():
            post_data.setdefault(field, value)
        form = SubscriptionRenewalForm(post_data, gym=gym, current_plan=plan)
        if form.is_valid():
            values = form.cleaned_data
            try:
                new_sub, payment = renew_with_payment(
                    subscription,
                    plan=values['plan'],
                    total_amount=values['total_amount'],
                    discount_amount=values['discount_amount'],
                    amount_paid=values['amount_paid'],
                    payment_method=values['payment_method'],
                    reference_number=values['reference_number'],
                    start_date=values['start_date'],
                    end_date=values['end_date'],
                    recorded_by=request.user,
                )
            except IntegrityError:
                form.add_error(None, 'This renewal has already been created for these dates.')
            except ValueError as exc:
                form.add_error(None, str(exc))
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                AuditLog.log_action(
                    actor=request.user, action='update', gym=gym, target=subscription,
                    metadata={'action': 'renewed', 'new_subscription_id': new_sub.id},
                )
                if payment:
                    AuditLog.log_action(actor=request.user, action='payment_recorded', gym=gym, target=payment)
                note = f'Membership renewed until {new_sub.end_date:%d %b %Y}.'
                if payment:
                    note += f' \u20b9{payment.amount} received.'
                due = new_sub.outstanding_balance
                if due > 0:
                    note += f' \u20b9{due} still due.'
                messages.success(request, note)
                return redirect('member_detail', member_id=subscription.member.member_id)
    else:
        form = SubscriptionRenewalForm(gym=gym, current_plan=plan, initial=initial)

    plans_json = [
        {'id': p.id, 'price': str(p.price), 'days': p.duration_days}
        for p in form.fields['plan'].queryset
    ]
    membership_history = MemberSubscription.objects.filter(
        member=subscription.member,
        gym=gym,
    ).select_related('plan').annotate(
        payments_received=Sum(
            'payments__amount',
            filter=Q(payments__status='completed'),
        ),
    ).order_by('-start_date', '-id')
    payment_history = subscription.member.payments.select_related(
        'subscription',
        'subscription__plan',
    ).order_by('-payment_date', '-id')
    context = {
        'gym': gym,
        'subscription': subscription,
        'form': form,
        'plans_json': plans_json,
        'membership_history': membership_history,
        'payment_history': payment_history,
        'method_choices': Payment.METHOD_CHOICES,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'memberships/renew.html', context)
