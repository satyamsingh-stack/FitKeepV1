"""
Reports views: dashboard and analytics.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Sum
from zoneinfo import ZoneInfo

from gyms.services import get_user_gym, get_user_role, can_manage_members
from members.models import MemberProfile
from attendance.models import Attendance
from memberships.models import MemberSubscription
from payments.models import Payment


@login_required
def dashboard_view(request):
    """Main dashboard with gym-local metrics and real attendance data."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view the gym dashboard.')

    from attendance.services import get_gym_local_date
    from retention.services import get_at_risk_members
    from django.db.models import Sum
    from datetime import timedelta
    from zoneinfo import ZoneInfoNotFoundError

    from memberships.services import sync_subscription_statuses
    from memberships.models import MembershipPlan
    from payments.services import get_outstanding_payments
    sync_subscription_statuses(gym=gym)
    today = get_gym_local_date(gym)
    week_ago = today - timedelta(days=6)
    month_ago = today - timedelta(days=29)
    total_members = MemberProfile.objects.filter(gym=gym, status='active').count()
    gym_now = timezone.now().astimezone(ZoneInfo(gym.timezone)) if getattr(gym, 'timezone', None) else timezone.now()
    occupancy_window_start = gym_now - timedelta(hours=12)
    live_occupancy = Attendance.objects.filter(
        gym=gym,
        check_in_at__gte=occupancy_window_start,
        check_in_at__lte=gym_now,
    ).count()
    today_attendance = Attendance.objects.filter(gym=gym, check_in_date=today).count()
    week_attendance = Attendance.objects.filter(gym=gym, check_in_date__gte=week_ago, check_in_date__lte=today).count()
    active_subscriptions = MemberSubscription.objects.filter(
        gym=gym, status='active', start_date__lte=today, end_date__gte=today
    ).count()
    month_revenue = Payment.objects.filter(
        member__gym=gym, payment_date__date__gte=month_ago, payment_date__date__lte=today, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0

    daily_rows = Attendance.objects.filter(
        gym=gym, check_in_date__gte=week_ago, check_in_date__lte=today
    ).values('check_in_date').annotate(count=Count('id'))
    counts_by_date = {row['check_in_date']: row['count'] for row in daily_rows}
    daily_labels = []
    daily_values = []
    for offset in range(7):
        day = week_ago + timedelta(days=offset)
        daily_labels.append(day.strftime('%a'))
        daily_values.append(counts_by_date.get(day, 0))

    at_risk_members = get_at_risk_members(gym)
    at_risk_count = at_risk_members.count()
    expiring_soon = MemberSubscription.objects.filter(
        gym=gym, status='active', end_date__gte=today, end_date__lte=today + timedelta(days=7)
    ).count()

    # "Needs attention" lists: the people the admin should act on today, each with a one-click action.
    expiring_list = list(
        MemberSubscription.objects.filter(
            gym=gym, status='active', end_date__gte=today, end_date__lte=today + timedelta(days=7)
        ).select_related('member', 'plan').order_by('end_date')[:5]
    )
    for sub in expiring_list:
        sub.days_left = (sub.end_date - today).days
    outstanding_all = get_outstanding_payments(gym)
    outstanding_list = sorted(outstanding_all, key=lambda item: item['outstanding'], reverse=True)[:5]
    outstanding_total = sum((item['outstanding'] for item in outstanding_all), 0)
    has_plans = MembershipPlan.objects.filter(gym=gym).exists()
    has_members = MemberProfile.objects.filter(gym=gym).exists()
    has_attendance = Attendance.objects.filter(gym=gym).exists()

    context = {
        'gym': gym,
        'expiring_list': expiring_list,
        'outstanding_list': outstanding_list,
        'outstanding_count': len(outstanding_all),
        'outstanding_total': outstanding_total,
        'has_plans': has_plans,
        'has_members': has_members,
        'has_attendance': has_attendance,
        'setup_incomplete': not (has_plans and has_members and has_attendance),
        'total_members': total_members,
        'live_occupancy': live_occupancy,
        'today_attendance': today_attendance,
        'week_attendance': week_attendance,
        'active_subscriptions': active_subscriptions,
        'month_revenue': month_revenue,
        'daily_labels': daily_labels,
        'daily_values': daily_values,
        'at_risk_count': at_risk_count,
        'expiring_soon': expiring_soon,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'reports/dashboard.html', context)


@login_required
def reports_view(request):
    """Detailed reports page."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view reports.')
    context = {
        'gym': gym,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'reports/reports.html', context)