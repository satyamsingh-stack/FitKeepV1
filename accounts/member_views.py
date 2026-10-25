"""
Member portal views.
"""
from django.contrib.auth.decorators import login_required
from datetime import date
import calendar as calendar_module
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from attendance.models import Attendance
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.models import GymMembership
from gyms.services import get_active_subscription, get_member_by_phone, get_user_gym
from attendance.services import get_streak_data, get_monthly_attendance, get_achievements


def _get_attendance_calendar_data(member, gym):
    attendance_dates = set(Attendance.objects.filter(member=member).values_list('check_in_date', flat=True))
    try:
        today = timezone.localtime(timezone.now(), ZoneInfo(gym.timezone or 'UTC')).date()
    except (ZoneInfoNotFoundError, ValueError):
        today = timezone.localdate()
    calendar_days = [
        {
            'date': date(today.year, today.month, day_number) if day_number else None,
            'is_today': day_number == today.day,
        }
        for week in calendar_module.monthcalendar(today.year, today.month)
        for day_number in week
    ]
    return attendance_dates, calendar_days, today


def _get_member_for_request(request, gym):
    """Resolve the authenticated user's own member profile within their active gym membership."""
    from members.models import MemberProfile
    allowed = GymMembership.objects.filter(user=request.user, gym=gym, role='member', status='active').exists()
    if not allowed:
        return None
    query = MemberProfile.objects.filter(gym=gym, status='active')
    member = query.filter(phone=request.user.phone).first() if request.user.phone else None
    if not member and request.user.email:
        member = query.filter(email__iexact=request.user.email).first()
    return member


@login_required
def member_dashboard(request):
    """Member home page showing attendance, streaks, and membership status."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return render(request, 'error/no_gym.html', status=403)

    membership = GymMembership.objects.filter(user=request.user, gym=gym, role='member', status='active').first()
    if not membership:
        return render(request, 'error/not_member.html', status=403)

    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', status=404)

    streak_data = get_streak_data(member)
    monthly_data = get_monthly_attendance(member)
    achievements = get_achievements(member)
    subscription = get_active_subscription(member)

    context = {
        'gym': gym,
        'member': member,
        'membership': membership,
        'streak_data': streak_data,
        'monthly_data': monthly_data,
        'achievements': achievements[:5],
        'subscription': subscription,
        'total_visits': Attendance.objects.filter(member=member).count(),
    }
    return render(request, 'member/dashboard.html', context)


def member_check_in(request):
    """Check in a member using a valid gym QR code and their account or registered phone."""
    from attendance.models import AttendanceQRCode

    token = (request.POST.get('qr_token') or request.GET.get('qr') or '').strip()
    qr = AttendanceQRCode.objects.filter(token_hash=token, is_active=True).select_related('gym').first() if token else None
    if not qr or not qr.is_valid:
        return render(request, 'error/invalid_qr.html', {'gym': None}, status=403)
    gym = qr.gym
    member = _get_member_for_request(request, gym) if request.user.is_authenticated else None
    account_member = member is not None

    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        if not member:
            member = get_member_by_phone(gym, phone)
        if not member or member.status != 'active':
            return JsonResponse({'success': False, 'error': 'No active member was found with that registered phone number.'}, status=404)

        if not get_active_subscription(member):
            return JsonResponse({
                'success': False,
                'error': 'Membership is not currently valid. Please contact the gym reception.',
            }, status=403)

        from attendance.services import check_in_member, get_gym_local_date
        result = check_in_member(
            member,
            gym,
            source='qr',
            verified=account_member,
            verification_method='member_account' if account_member else 'registered_phone',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        if result.get('success'):
            monthly_data = get_monthly_attendance(member)
            result.update({
                'today_status': 'Checked in',
                'current_streak': get_streak_data(member)['current_streak'],
                'monthly_visits': monthly_data[-1]['count'],
                'check_in_date': get_gym_local_date(gym).isoformat(),
            })
        elif result.get('attendance_id'):
            request.session['check_in_summary'] = {
                'gym_id': gym.id,
                'member_id': member.id,
            }
            return JsonResponse({
                'success': True,
                'already_checked_in': True,
                'summary_url': reverse('member:check_in_summary'),
            })
        return JsonResponse(result, status=200 if result.get('success') else 400)
    return render(request, 'member/check_in.html', {
        'gym': gym,
        'qr_token': token,
        'member': member,
        'is_authenticated_member': member is not None,
    })


def member_check_in_summary(request):
    """Show today's attendance, streak, and monthly calendar after a duplicate QR check-in."""
    summary = request.session.get('check_in_summary')
    if not summary:
        return redirect('landing')

    from gyms.models import Gym
    from members.models import MemberProfile

    gym = Gym.objects.filter(pk=summary.get('gym_id')).first()
    member = MemberProfile.objects.filter(pk=summary.get('member_id'), gym=gym, status='active').first() if gym else None
    if not member:
        request.session.pop('check_in_summary', None)
        return redirect('landing')

    attendance_dates, calendar_days, today = _get_attendance_calendar_data(member, gym)
    if today not in attendance_dates:
        request.session.pop('check_in_summary', None)
        return redirect('landing')

    return render(request, 'member/check_in_summary.html', {
        'gym': gym,
        'member': member,
        'attendance_dates': attendance_dates,
        'calendar_days': calendar_days,
        'calendar_month': today,
        'streak_data': get_streak_data(member),
    })


@login_required
def member_attendance_history(request):
    gym = get_user_gym(request.user)
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    records = Attendance.objects.filter(member=member).order_by('-check_in_at')
    return render(request, 'member/attendance_history.html', {'member': member, 'records': records, 'gym': gym})


@login_required
def member_attendance_calendar(request):
    gym = get_user_gym(request.user)
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    attendance_dates, calendar_days, today = _get_attendance_calendar_data(member, gym)
    return render(request, 'member/calendar.html', {'member': member, 'gym': gym, 'attendance_dates': attendance_dates, 'calendar_days': calendar_days, 'calendar_month': today})


@login_required
def member_streaks(request):
    gym = get_user_gym(request.user)
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    streak_data = get_streak_data(member)
    return render(request, 'member/streaks.html', {'member': member, 'streak_data': streak_data, 'gym': gym})


@login_required
def member_achievements(request):
    gym = get_user_gym(request.user)
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    achievements = get_achievements(member)
    return render(request, 'member/achievements.html', {'member': member, 'achievements': achievements, 'gym': gym})


@login_required
def member_membership(request):
    gym = get_user_gym(request.user)
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    subscription = get_active_subscription(member)
    return render(request, 'member/membership.html', {'member': member, 'subscription': subscription, 'gym': gym})


@login_required
def member_payments(request):
    gym = get_user_gym(request.user)
    from payments.models import Payment
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    payments = Payment.objects.filter(member=member).order_by('-payment_date')
    return render(request, 'member/payments.html', {'member': member, 'payments': payments, 'gym': gym})


@login_required
def member_profile(request):
    gym = get_user_gym(request.user)
    member = _get_member_for_request(request, gym)
    if not member:
        return render(request, 'error/member_not_found.html', {'gym': gym}, status=404)
    return render(request, 'member/profile.html', {'member': member, 'gym': gym})