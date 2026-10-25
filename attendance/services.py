"""
Attendance service module: check-in logic, streaks, achievements.
"""
from datetime import timedelta
from django.db.models import Count, Sum, Q
from django.utils import timezone

from attendance.models import Attendance, AttendanceQRCode
from members.models import MemberProfile


def get_gym_local_date(gym, value=None):
    """Return a timestamp's calendar date in the gym's configured timezone."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    value = value or timezone.now()
    try:
        tz = ZoneInfo(gym.timezone or 'UTC')
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo('UTC')
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone=tz)
    return timezone.localtime(value, tz).date()


def check_in_member(member, gym, source='qr', verified=False, recorded_by=None, notes='', ip_address=None, verification_method=''):
    """Record one attendance per member per local gym calendar day."""
    from django.db import IntegrityError, transaction

    if member.gym_id != gym.id:
        return {'success': False, 'error': 'This member does not belong to this gym.'}
    if member.status != 'active':
        return {'success': False, 'error': 'This member is not currently active.'}

    now = timezone.now()
    local_date = get_gym_local_date(gym, now)
    from memberships.services import sync_subscription_statuses
    sync_subscription_statuses(member=member)
    # If the gym uses subscription records for this member, require a valid period.
    subscriptions = member.subscriptions.all()
    if subscriptions.exists() and not subscriptions.filter(status='active', start_date__lte=local_date, end_date__gte=local_date).exists():
        return {'success': False, 'error': 'Membership is not currently valid. Please contact the gym reception.'}

    existing = Attendance.objects.filter(member=member, gym=gym, check_in_date=local_date).first()
    if existing:
        streak = get_streak_data(member)
        return {'success': False, 'error': f"Already checked in today. Current streak: {streak['current_streak']} days.", 'attendance_id': existing.id, 'streak': streak}

    try:
        with transaction.atomic():
            attendance = Attendance.objects.create(
                gym=gym,
                member=member,
                check_in_at=now,
                check_in_date=local_date,
                source=source,
                verification_status='verified' if verified else 'unverified',
                verification_method=verification_method,
                recorded_by=recorded_by,
                notes=notes,
                ip_address=ip_address,
            )
            member.last_attendance_date = now
            member.save(update_fields=['last_attendance_date'])
    except IntegrityError:
        # A concurrent request may have inserted the daily record after our first check.
        existing = Attendance.objects.filter(member=member, gym=gym, check_in_date=local_date).first()
        streak = get_streak_data(member)
        return {'success': False, 'error': f"Already checked in today. Current streak: {streak['current_streak']} days.", 'attendance_id': existing.id if existing else None, 'streak': streak}

    return {
        'success': True,
        'attendance_id': attendance.id,
        'check_in_at': attendance.check_in_at.isoformat(),
        'message': 'Check-in successful!',
    }


def get_streak_data(member):
    """
    Calculate current and longest attendance streak for a member.
    """
    records = Attendance.objects.filter(member=member).order_by('-check_in_date')
    if not records:
        return {'current_streak': 0, 'longest_streak': 0, 'last_check_in': None, 'streak_active': False}

    dates = [r.check_in_date for r in records]
    unique_dates = sorted(set(dates), reverse=True)

    current_streak = 0
    longest_streak = 0
    temp_streak = 1
    today = get_gym_local_date(member.gym)
    yesterday = today - timedelta(days=1)

    if unique_dates[0] == today or unique_dates[0] == yesterday:
        current_streak = 1
        for i in range(1, len(unique_dates)):
            if (unique_dates[i-1] - unique_dates[i]).days == 1:
                current_streak += 1
            else:
                break
        if unique_dates[0] != today and unique_dates[0] != yesterday:
            current_streak = 0

    for i in range(1, len(unique_dates)):
        if (unique_dates[i-1] - unique_dates[i]).days == 1:
            temp_streak += 1
        else:
            longest_streak = max(longest_streak, temp_streak)
            temp_streak = 1
    longest_streak = max(longest_streak, temp_streak)

    return {
        'current_streak': current_streak,
        'longest_streak': longest_streak,
        'last_check_in': records.first().check_in_at,
        'streak_active': unique_dates[0] == today or unique_dates[0] == yesterday,
    }


def get_monthly_attendance(member, months=6):
    """
    Get monthly attendance counts for the last N months.
    """
    today = get_gym_local_date(member.gym)
    monthly_data = []
    for i in range(months - 1, -1, -1):
        month_index = today.year * 12 + (today.month - 1) - i
        year, month0 = divmod(month_index, 12)
        first_day = today.replace(year=year, month=month0 + 1, day=1)
        if i == 0:
            last_day = today
        else:
            next_index = year * 12 + month0 + 1
            next_year, next_month0 = divmod(next_index, 12)
            last_day = today.replace(year=next_year, month=next_month0 + 1, day=1) - timedelta(days=1)

        count = Attendance.objects.filter(
            member=member,
            check_in_date__gte=first_day,
            check_in_date__lte=last_day,
        ).count()

        monthly_data.append({
            'month': first_day.strftime('%b %Y'),
            'count': count,
        })
    return monthly_data


def get_achievements(member):
    """
    Compute achievements for a member based on attendance patterns.
    """
    achievements = []
    streak = get_streak_data(member)
    total_visits = Attendance.objects.filter(member=member).count()

    if total_visits >= 1:
        achievements.append({'name': 'First Visit', 'icon': '🎯', 'earned': True})
    if streak['current_streak'] >= 7:
        achievements.append({'name': 'Week Warrior', 'icon': '🔥', 'earned': True})
    if streak['current_streak'] >= 30:
        achievements.append({'name': 'Monthly Master', 'icon': '💪', 'earned': True})
    if streak['longest_streak'] >= 30:
        achievements.append({'name': 'Streak Legend', 'icon': '🏆', 'earned': True})
    if total_visits >= 100:
        achievements.append({'name': 'Century Club', 'icon': '💯', 'earned': True})
    if total_visits >= 365:
        achievements.append({'name': 'Year Round', 'icon': '📅', 'earned': True})

    return achievements


def generate_qr_code(gym):
    """
    Generate a new active QR code for a gym, revoking any previous active one.
    """
    from django.conf import settings
    AttendanceQRCode.objects.filter(gym=gym, is_active=True).update(is_active=False)
    token = AttendanceQRCode.generate_token()
    expiry_hours = getattr(settings, 'QR_CODE_EXPIRY_HOURS', 24)
    qr = AttendanceQRCode.objects.create(
        gym=gym,
        token_hash=token,
        is_active=True,
        expires_at=timezone.now() + timedelta(hours=expiry_hours),
    )
    return qr


def get_active_qr(gym):
    """Get the currently active QR code for a gym."""
    return AttendanceQRCode.get_active_qr(gym)