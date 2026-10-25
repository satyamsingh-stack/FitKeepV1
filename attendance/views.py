"""
Attendance views: check-in, QR codes, history, reports.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta

from attendance.models import Attendance, AttendanceQRCode
from attendance.services import check_in_member, generate_qr_code, get_active_qr, get_monthly_attendance, get_gym_local_date
from members.models import MemberProfile
from gyms.services import can_manage_members, get_user_gym, get_user_role, get_member_by_phone
from audit.models import AuditLog


@login_required
def attendance_home(request):
    """Attendance home page."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view gym attendance.')
    today = get_gym_local_date(gym)
    today_count = Attendance.objects.filter(gym=gym, check_in_date=today).count()
    qr = get_active_qr(gym)

    context = {
        'gym': gym,
        'today_count': today_count,
        'qr_code': qr,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'attendance/home.html', context)


@login_required
def check_in(request):
    """Check-in endpoint (AJAX-friendly)."""
    if request.method == 'POST':
        try:
            gym = get_user_gym(request.user)
        except Exception:
            return JsonResponse({'success': False, 'error': 'No gym access.'}, status=403)

        if not can_manage_members(request.user, gym):
            return JsonResponse({'success': False, 'error': 'You do not have permission to record front-desk attendance.'}, status=403)
        phone = request.POST.get('phone', '').strip()
        member = get_member_by_phone(gym, phone)
        if not member:
            return JsonResponse({'success': False, 'error': 'Member not found.'}, status=404)

        # This endpoint is the staff front-desk flow; QR self-check-in uses the member portal endpoint.
        manual = True
        result = check_in_member(
            member=member,
            gym=gym,
            source='manual' if manual else 'qr',
            verified=manual,
            recorded_by=request.user if manual else None,
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        if result['success']:
            AuditLog.log_action(
                actor=request.user,
                action='member_checkin',
                gym=gym,
                target=member,
                metadata={'attendance_id': result.get('attendance_id')},
            )

        return JsonResponse(result)

    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')
    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to use front-desk check-in.')
    return render(request, 'attendance/check_in.html', {'gym': gym})


@login_required
def qr_display(request):
    """Display the active QR code for scanning."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to manage the gym QR code.')
    qr = get_active_qr(gym)
    context = {
        'gym': gym,
        'qr_code': qr,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'attendance/qr_display.html', context)


@login_required
def qr_generate(request):
    """Generate a new QR code."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return JsonResponse({'success': False, 'error': 'No gym access.'}, status=403)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if not can_manage_members(request.user, gym):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    qr = generate_qr_code(gym)
    AuditLog.log_action(actor=request.user, action='qr_generated', gym=gym, target=qr)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'qr_id': qr.id})
    messages.success(request, 'A new attendance QR code is ready. The previous code has been deactivated.')
    return redirect('qr_display')


@login_required
def attendance_history(request):
    """View attendance history."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view attendance history.')
    records = Attendance.objects.filter(gym=gym).order_by('-check_in_at')
    member_filter = request.GET.get('member', '')
    date_filter = request.GET.get('date', get_gym_local_date(gym).isoformat())

    if member_filter:
        records = records.filter(member__full_name__icontains=member_filter)
    if date_filter:
        records = records.filter(check_in_date=date_filter)

    context = {
        'gym': gym,
        'records': records,
        'member_filter': member_filter,
        'date_filter': date_filter,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'attendance/history.html', context)


@login_required
def attendance_report(request):
    """Attendance report with charts."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view attendance reports.')
    today = get_gym_local_date(gym)
    try:
        days = max(7, min(int(request.GET.get('days', 30)), 365))
    except (TypeError, ValueError):
        days = 30
    start_date = today - timedelta(days=days)

    from django.db.models import Count
    daily_counts = (
        Attendance.objects.filter(gym=gym, check_in_date__gte=start_date)
        .values('check_in_date')
        .annotate(count=Count('id'))
        .order_by('check_in_date')
    )

    total_visits = sum(d['count'] for d in daily_counts)
    active_members = Attendance.objects.filter(
        gym=gym, check_in_date__gte=start_date
    ).values('member').distinct().count()

    context = {
        'gym': gym,
        'daily_counts': list(daily_counts),
        'total_visits': total_visits,
        'active_members': active_members,
        'days': days,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'attendance/report.html', context)