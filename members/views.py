"""
Member views: CRUD operations for gym members.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.paginator import Paginator
from django.db.models import Q
from decimal import Decimal

from members.models import MemberProfile
from members.forms import MemberForm, MemberOnboardingForm
from members.services import onboard_member
from gyms.services import can_manage_members, get_user_gym, get_user_role, get_member_by_id
from audit.models import AuditLog


@login_required
def member_list(request):
    """List members for the user's gym."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('Access denied.')

    from memberships.models import MemberSubscription
    from memberships.services import sync_subscription_statuses
    from payments.services import get_outstanding_payments
    from datetime import timedelta
    from attendance.services import default_check_out_input, get_gym_local_date

    sync_subscription_statuses(gym=gym)
    today = get_gym_local_date(gym)
    members = MemberProfile.objects.filter(gym=gym).order_by('-joined_at')
    search = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    membership_filter = request.GET.get('membership', '')

    if search:
        members = members.filter(
            Q(full_name__icontains=search) |
            Q(member_id__icontains=search) |
            Q(phone__icontains=search) |
            Q(email__icontains=search)
        )
    if status_filter:
        members = members.filter(status=status_filter)

    # Membership filters: the questions an admin actually asks ("who needs renewing?", "who owes us?")
    live = MemberSubscription.objects.filter(gym=gym, status='active', start_date__lte=today, end_date__gte=today)
    if membership_filter == 'active':
        members = members.filter(id__in=live.values('member_id'))
    elif membership_filter == 'expiring':
        members = members.filter(id__in=live.filter(end_date__lte=today + timedelta(days=7)).values('member_id'))
    elif membership_filter == 'expired':
        members = members.exclude(id__in=live.values('member_id')).filter(subscriptions__isnull=False).distinct()
    elif membership_filter == 'none':
        members = members.filter(subscriptions__isnull=True)
    elif membership_filter == 'due':
        due_ids = {item['member'].id for item in get_outstanding_payments(gym)}
        members = members.filter(id__in=due_ids)

    paginator = Paginator(members, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Attach each member's latest membership (one query) so the table can show validity and dues.
    page_members = list(page_obj.object_list)
    latest = {}
    for sub in MemberSubscription.objects.filter(member__in=page_members).select_related('plan').order_by('end_date'):
        latest[sub.member_id] = sub
    for m in page_members:
        m.current_sub = latest.get(m.id)
    page_obj.object_list = page_members

    context = {
        'gym': gym,
        'members': page_obj,
        'search': search,
        'status_filter': status_filter,
        'membership_filter': membership_filter,
        'default_check_out': default_check_out_input(gym),
        'status_choices': MemberProfile.STATUS_CHOICES,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'members/list.html', context)


@login_required
def member_add(request):
    """Onboard a new member: profile, membership and first payment in one step."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to add members.')

    if request.method == 'POST':
        form = MemberOnboardingForm(request.POST, request.FILES, gym=gym)
        if form.is_valid():
            data = form.cleaned_data
            member = form.save(commit=False)
            member.status = 'active'
            try:
                result = onboard_member(
                    gym=gym,
                    member=member,
                    plan=data.get('plan'),
                    start_date=data.get('start_date'),
                    end_date=data.get('end_date'),
                    total_amount=data.get('total_amount'),
                    discount_amount=data.get('discount_amount'),
                    amount_paid=data.get('amount_paid'),
                    payment_method=data.get('payment_method') or 'cash',
                    reference_number=data.get('reference_number', ''),
                    recorded_by=request.user,
                )
            except IntegrityError:
                UserModel = get_user_model()
                email = data.get('email')
                email_conflict = bool(
                    email
                    and UserModel.objects.filter(email__iexact=email)
                    .exclude(username=data.get('phone'))
                    .exists()
                )
                if not email_conflict:
                    raise
                form.add_error(
                    'email',
                    'This email is already linked to another account. Use a different email address or leave it blank.',
                )
            else:
                subscription = result['subscription']
                AuditLog.log_action(actor=request.user, action='create', gym=gym, target=member)
                if subscription:
                    AuditLog.log_action(
                        actor=request.user, action='create', gym=gym, target=subscription,
                        metadata={'source': 'onboarding'},
                    )
                if result['payment']:
                    AuditLog.log_action(actor=request.user, action='payment_recorded', gym=gym, target=result['payment'])

                parts = [f'{member.full_name} was added']
                if subscription:
                    parts.append(f'with a membership valid until {subscription.end_date:%d %b %Y}')
                    if result['payment']:
                        parts.append(f'and \u20b9{result["payment"].amount} received')
                    due = subscription.outstanding_balance
                    if due > 0:
                        parts.append(f'(\u20b9{due} still due)')
                message = ' '.join(parts) + '.'
                if result['temporary_password']:
                    message += (
                        f' Temporary member-portal password: {result["temporary_password"]} '
                        '\u2014 share it privately and ask the member to change it after signing in.'
                    )
                else:
                    message += ' An existing account was linked to this gym.'
                messages.success(request, message)
                return redirect('member_detail', member_id=member.member_id)
    else:
        form = MemberOnboardingForm(gym=gym)

    plans = [
        {
            'id': plan.id,
            'name': plan.name,
            'price': str(plan.price),
            'days': plan.duration_days,
            'label': plan.duration_label,
        }
        for plan in form.fields['plan'].queryset
    ]
    context = {
        'gym': gym,
        'form': form,
        'plans': plans,
        'plans_json': plans,
        'selected_plan': form['plan'].value() or '',
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'members/onboard.html', context)


@login_required
def member_detail(request, member_id):
    """View member details."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view member details.')
    member = get_object_or_404(MemberProfile, gym=gym, member_id=member_id)

    from attendance.models import Attendance
    from memberships.models import MemberSubscription
    from memberships.services import sync_subscription_statuses
    sync_subscription_statuses(member=member)
    member.refresh_from_db()
    from payments.models import Payment
    from retention.models import ContactLog

    attendance_records = Attendance.objects.filter(member=member).order_by('-check_in_at')[:10]
    subscriptions = MemberSubscription.objects.filter(member=member).order_by('-start_date')[:5]
    payments = Payment.objects.filter(member=member).order_by('-payment_date')[:10]
    contact_logs = ContactLog.objects.filter(member=member).order_by('-contact_date')[:10]

    latest_subscription = subscriptions[0] if subscriptions else None
    total_due = sum(
        (sub.outstanding_balance for sub in MemberSubscription.objects.filter(member=member, status__in=['active', 'pending'])),
        Decimal('0.00'),
    )
    from attendance.services import get_gym_local_date
    checked_in_today = Attendance.objects.filter(member=member, check_in_date=get_gym_local_date(gym)).exists()
    from attendance.services import default_check_out_input

    context = {
        'gym': gym,
        'member': member,
        'latest_subscription': latest_subscription,
        'total_due': total_due,
        'checked_in_today': checked_in_today,
        'default_check_out': default_check_out_input(gym),
        'attendance_records': attendance_records,
        'subscriptions': subscriptions,
        'payments': payments,
        'contact_logs': contact_logs,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'members/detail.html', context)


@login_required
def member_edit(request, member_id):
    """Edit member details."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to edit members.')

    member = get_object_or_404(MemberProfile, gym=gym, member_id=member_id)

    if request.method == 'POST':
        form = MemberForm(request.POST, request.FILES, instance=member)
        if form.is_valid():
            form.save()

            AuditLog.log_action(
                actor=request.user,
                action='update',
                gym=gym,
                target=member,
            )
            messages.success(request, 'Member updated successfully.')
            return redirect('member_detail', member_id=member.member_id)
    else:
        form = MemberForm(instance=member)

    context = {
        'gym': gym,
        'form': form,
        'member': member,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'members/form.html', context)


@login_required
def member_check_in(request, member_id):
    """Front-desk: mark a member present with one click (from the member list or profile)."""
    if request.method != 'POST':
        return redirect('member_detail', member_id=member_id)
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')
    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to record attendance.')

    member = get_object_or_404(MemberProfile, gym=gym, member_id=member_id)
    from attendance.services import check_in_member, default_check_out_input, parse_gym_datetime
    check_out_value = request.POST.get('check_out_at') or default_check_out_input(gym)
    check_out_at = parse_gym_datetime(check_out_value, gym)
    if not check_out_at:
        messages.error(request, 'Enter a valid end time later than now.')
        return redirect('member_detail', member_id=member_id)
    result = check_in_member(
        member=member, gym=gym, check_out_at=check_out_at, source='manual', verified=True,
        recorded_by=request.user, ip_address=request.META.get('REMOTE_ADDR'),
    )
    if result['success']:
        AuditLog.log_action(
            actor=request.user, action='member_checkin', gym=gym, target=member,
            metadata={'attendance_id': result.get('attendance_id')},
        )
        messages.success(request, f'{member.full_name} marked present.')
    else:
        messages.warning(request, f'{member.full_name}: {result["error"]}')

    next_url = request.POST.get('next', '')
    from django.utils.http import url_has_allowed_host_and_scheme
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect('member_detail', member_id=member.member_id)


@login_required
def member_reset_password(request, member_id):
    """Issue a new temporary member-portal password (e.g. when the first one was lost)."""
    if request.method != 'POST':
        return redirect('member_detail', member_id=member_id)
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')
    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to reset member passwords.')

    member = get_object_or_404(MemberProfile, gym=gym, member_id=member_id)
    from django.contrib.auth import get_user_model
    import secrets
    User = get_user_model()
    candidates = [v for v in (member.phone, member.email) if v]
    user = None
    if candidates:
        user = User.objects.filter(
            role='gym_member', gym_memberships__gym=gym, username__in=candidates,
        ).first()
    if not user:
        messages.error(request, 'No member-portal account was found for this member.')
        return redirect('member_detail', member_id=member.member_id)

    temporary_password = secrets.token_urlsafe(12)
    user.set_password(temporary_password)
    user.save()
    AuditLog.log_action(actor=request.user, action='update', gym=gym, target=member, metadata={'action': 'portal_password_reset'})
    messages.success(
        request,
        f'New temporary password for {member.full_name}: {temporary_password} \u2014 share it privately and ask them to change it after signing in.',
    )
    return redirect('member_detail', member_id=member.member_id)
