"""
Reports views: dashboard and analytics.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from datetime import timedelta
from django.db.models import Count, Q, Sum
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
    sync_subscription_statuses(gym=gym)
    today = get_gym_local_date(gym)
    week_ago = today - timedelta(days=6)
    month_ago = today - timedelta(days=29)
    active_members = MemberSubscription.objects.filter(
        gym=gym,
        status='active',
        start_date__lte=today,
        end_date__gte=today,
    ).values('member_id').distinct().count()
    gym_now = timezone.now().astimezone(ZoneInfo(gym.timezone)) if getattr(gym, 'timezone', None) else timezone.now()
    occupancy_window_start = gym_now - timedelta(hours=2)
    live_occupancy = Attendance.objects.filter(
        gym=gym,
        check_in_at__gte=occupancy_window_start,
        check_in_at__lte=gym_now,
    ).filter(
        Q(check_out_at__gt=gym_now)
        | Q(check_out_at__isnull=True)
    ).count()
    today_attendance = Attendance.objects.filter(gym=gym, check_in_date=today).count()
    week_attendance = Attendance.objects.filter(gym=gym, check_in_date__gte=week_ago, check_in_date__lte=today).count()
    active_subscriptions = MemberSubscription.objects.filter(
        gym=gym, status='active', start_date__lte=today, end_date__gte=today
    ).count()
    expired_subscriptions = MemberSubscription.objects.filter(
        gym=gym,
        status='expired',
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

    chart_max = max(daily_values, default=0) or 1
    chart_points = [
        (50 + index * 100, 220 - round(value / chart_max * 170), label, value)
        for index, (label, value) in enumerate(zip(daily_labels, daily_values))
    ]
    chart_grid = format_html_join(
        '',
        '<line x1="46" y1="{}" x2="654" y2="{}" stroke="#eef0f5" stroke-width="1"/>',
        ((y, y) for y in (20, 70, 120, 170, 220)),
    )
    chart_line = ' '.join(f'{x},{y}' for x, y, _, _ in chart_points)
    chart_dots = format_html_join(
        '',
        '<circle cx="{}" cy="{}" r="5" fill="#635bff" stroke="#fff" stroke-width="2"><title>{}: {} visits</title></circle>',
        ((x, y, label, value) for x, y, label, value in chart_points),
    )
    chart_labels = format_html_join(
        '',
        '<text x="{}" y="258" text-anchor="middle" fill="#6b7280" font-size="12">{}</text>',
        ((x, label) for x, _, label, _ in chart_points),
    )
    attendance_chart_svg = format_html(
        '<svg id="attendance-trend-chart" xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 700 280" preserveAspectRatio="none" role="img" '
        'aria-label="Attendance for the last seven days">'
        '<title>Attendance trend for the last seven days</title>'
        '<g>{}</g>'
        '<polyline points="{}" fill="none" stroke="#635bff" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '{}{}'
        '</svg>',
        chart_grid,
        chart_line,
        chart_dots,
        chart_labels,
    )

    at_risk_members = get_at_risk_members(gym)
    at_risk_count = at_risk_members.count()
    expiring_soon = MemberSubscription.objects.filter(
        gym=gym, status='active', end_date__gte=today, end_date__lte=today + timedelta(days=7)
    ).count()

    has_plans = MembershipPlan.objects.filter(gym=gym).exists()
    has_members = MemberProfile.objects.filter(gym=gym).exists()
    has_attendance = Attendance.objects.filter(gym=gym).exists()

    context = {
        'gym': gym,
        'has_plans': has_plans,
        'has_members': has_members,
        'has_attendance': has_attendance,
        'setup_incomplete': not (has_plans and has_members and has_attendance),
        'active_members': active_members,
        'live_occupancy': live_occupancy,
        'today_attendance': today_attendance,
        'week_attendance': week_attendance,
        'active_subscriptions': active_subscriptions,
        'expired_subscriptions': expired_subscriptions,
        'month_revenue': month_revenue,
        'attendance_chart_svg': attendance_chart_svg,
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