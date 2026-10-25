"""
Retention views: at-risk members, contact logging, reports.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta

from members.models import MemberProfile
from retention.models import ContactLog
from retention.services import (
    calculate_retention_risk,
    get_at_risk_members,
    get_retention_report,
    log_contact,
)
from gyms.services import can_manage_members, get_user_gym, get_user_role
from audit.models import AuditLog


@login_required
def retention_list(request):
    """List members at risk of churn."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view retention data.')
    risk_level = request.GET.get('risk_level', '')
    at_risk = get_at_risk_members(gym)
    if risk_level:
        at_risk = at_risk.filter(retention_risk_level=risk_level)

    context = {
        'gym': gym,
        'at_risk': at_risk,
        'risk_level': risk_level,
        'risk_levels': ['early_warning', 'at_risk', 'high_risk', 'critical'],
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'retention/list.html', context)


@login_required
def contact_log_create(request):
    """Log a member contact."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to log contacts.')

    if request.method == 'POST':
        member_id = request.POST.get('member_id', '').strip()
        outcome = request.POST.get('outcome', 'other')
        notes = request.POST.get('notes', '').strip()
        next_follow_up = request.POST.get('next_follow_up_days', '').strip()
        try:
            next_follow_up_days = int(next_follow_up) if next_follow_up else None
            if next_follow_up_days is not None and not 1 <= next_follow_up_days <= 365:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, 'Follow-up must be between 1 and 365 days.')
            return redirect('contact_log_create')

        member = get_object_or_404(MemberProfile, gym=gym, member_id=member_id)
        log_contact(
            member=member,
            gym=gym,
            outcome=outcome,
            notes=notes,
            contacted_by=request.user,
            next_follow_up_days=next_follow_up_days,
        )

        AuditLog.log_action(
            actor=request.user,
            action='contact_made',
            gym=gym,
            target=member,
            metadata={'outcome': outcome},
        )
        messages.success(request, 'Contact logged successfully.')
        return redirect('retention_list')

    members = MemberProfile.objects.filter(gym=gym, status='active').order_by('full_name')
    context = {
        'gym': gym,
        'members': members,
        'outcome_choices': ContactLog.OUTCOME_CHOICES,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'retention/contact_form.html', context)


@login_required
def contact_history(request):
    """View contact history for a member or all members."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view contact history.')
    member_id = request.GET.get('member', '')
    contacts = ContactLog.objects.filter(gym=gym).order_by('-contact_date')
    if member_id:
        contacts = contacts.filter(member__member_id=member_id)

    context = {
        'gym': gym,
        'contacts': contacts,
        'member_id': member_id,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'retention/contact_history.html', context)


@login_required
def retention_report(request):
    """Retention analytics report."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view retention reports.')
    try:
        days = max(7, min(int(request.GET.get('days', 30)), 365))
    except (TypeError, ValueError):
        days = 30
    report = get_retention_report(gym, days=days)

    context = {
        'gym': gym,
        'report': report,
        'days': days,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'retention/report.html', context)