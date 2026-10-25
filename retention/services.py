"""
Retention service module: risk calculation, contact logging, metrics.
"""
from datetime import datetime, timedelta
from django.db.models import Count, Q, Avg
from django.utils import timezone

from members.models import MemberProfile
from retention.models import RetentionRiskLevel, ContactLog, RetentionMetric


def calculate_retention_risk(member, gym):
    """Calculate and persist a member's current inactivity risk using gym settings."""
    from attendance.services import get_gym_local_date
    from accounts.models import GymSettings
    from django.contrib.auth import get_user_model
    from memberships.models import MemberSubscription

    settings_obj = GymSettings.objects.filter(gym=gym).first()
    now = timezone.now()
    today = get_gym_local_date(gym, now)
    reference = member.last_attendance_date
    if not reference:
        current_subscription = MemberSubscription.objects.filter(
            member=member,
            status='active',
            start_date__lte=today,
            end_date__gte=today,
        ).order_by('start_date').first()
        reference = current_subscription.start_date if current_subscription else member.joined_at
    reference_date = get_gym_local_date(gym, reference) if isinstance(reference, datetime) else reference
    days_inactive = max(0, (today - reference_date).days)

    # Prefer explicit custom risk levels when configured; otherwise use gym-level defaults.
    configured_levels = RetentionRiskLevel.objects.filter(gym=gym, is_active=True).order_by('min_days')
    if configured_levels.exists():
        risk_level = RetentionRiskLevel.get_risk_level(gym, days_inactive)
    else:
        thresholds = settings_obj or gym
        if days_inactive >= thresholds.inactivity_critical_risk_days:
            risk_level = 'critical'
        elif days_inactive >= thresholds.inactivity_high_risk_days:
            risk_level = 'high_risk'
        elif days_inactive >= thresholds.inactivity_at_risk_days:
            risk_level = 'at_risk'
        elif days_inactive >= thresholds.inactivity_early_warning_days:
            risk_level = 'early_warning'
        else:
            risk_level = 'none'

    previous_risk_level = member.retention_risk_level
    if previous_risk_level != risk_level or member.retention_risk_days != days_inactive:
        member.retention_risk_level = risk_level
        member.retention_risk_days = days_inactive
        member.save(update_fields=['retention_risk_level', 'retention_risk_days'])

    if risk_level != 'none':
        if not settings_obj or settings_obj.notify_inactivity:
            User = get_user_model()
            recipients = User.objects.filter(
                gym_memberships__gym=gym,
                gym_memberships__role__in=['owner', 'staff'],
                gym_memberships__status='active',
            ).distinct()
            from notifications.services import notify_inactivity
            notify_inactivity(
                member,
                gym,
                days_inactive,
                recipients,
                risk_level,
                skip_existing=risk_level == previous_risk_level,
            )
    return risk_level, days_inactive


def get_at_risk_members(gym, risk_levels=None):
    """Refresh risk values for eligible members, excluding expired subscriptions."""
    if risk_levels is None:
        risk_levels = ['early_warning', 'at_risk', 'high_risk', 'critical']
    from attendance.services import get_gym_local_date
    today = get_gym_local_date(gym)
    eligible_ids = []
    eligible = MemberProfile.objects.filter(gym=gym, status='active').only(
        'id', 'gym_id', 'status', 'joined_at', 'last_attendance_date', 'retention_risk_level', 'retention_risk_days'
    )
    for member in eligible.iterator():
        has_subscriptions = member.subscriptions.exists()
        has_valid_subscription = member.subscriptions.filter(
            status='active', start_date__lte=today, end_date__gte=today
        ).exists()
        if has_subscriptions and not has_valid_subscription:
            if member.retention_risk_level != 'none':
                member.retention_risk_level = 'none'
                member.save(update_fields=['retention_risk_level'])
            continue
        eligible_ids.append(member.pk)
        calculate_retention_risk(member, gym)
    return MemberProfile.objects.filter(
        gym=gym, status='active', id__in=eligible_ids, retention_risk_level__in=risk_levels
    ).order_by('-retention_risk_days', 'full_name')


def log_contact(member, gym, outcome, notes='', contacted_by=None, next_follow_up_days=None):
    """
    Log a member contact attempt.
    """
    contact = ContactLog.objects.create(
        gym=gym,
        member=member,
        contact_outcome=outcome,
        notes=notes,
        contacted_by=contacted_by,
        next_follow_up_date=(timezone.now().date() + timedelta(days=next_follow_up_days)) if next_follow_up_days else None,
    )
    member.last_contact_date = timezone.now()
    member.save(update_fields=['last_contact_date'])
    return contact


def get_contact_history(member, limit=50):
    """Get contact history for a member."""
    return ContactLog.objects.filter(member=member).order_by('-contact_date')[:limit]


def compute_retention_metrics(gym, period_start, period_end):
    """Compute retention outcomes using attendance after contact, not a manually selected label."""
    from attendance.models import Attendance
    from memberships.models import MemberSubscription

    at_risk = get_at_risk_members(gym).count()
    contacts = ContactLog.objects.filter(
        gym=gym, contact_date__date__gte=period_start, contact_date__date__lte=period_end
    ).select_related('member')
    contacted_member_ids = set(contacts.values_list('member_id', flat=True))
    returned_member_ids = set()
    for contact in contacts:
        returned = Attendance.objects.filter(
            gym=gym, member_id=contact.member_id,
            check_in_at__gt=contact.contact_date,
            check_in_date__lte=period_end,
        ).exists()
        if returned:
            returned_member_ids.add(contact.member_id)
    renewed = MemberSubscription.objects.filter(
        gym=gym, previous_subscription__isnull=False,
        created_at__date__gte=period_start, created_at__date__lte=period_end,
    ).values('member_id').distinct().count()

    metric, _ = RetentionMetric.objects.update_or_create(
        gym=gym,
        period_start=period_start,
        period_end=period_end,
        defaults={
            'total_at_risk': at_risk,
            'total_contacted': len(contacted_member_ids),
            'total_returned': len(returned_member_ids),
            'total_renewed': renewed,
        },
    )
    return metric


def get_retention_report(gym, days=30):
    """
    Generate a retention report for a gym.
    """
    today = timezone.now().date()
    period_start = today - timedelta(days=days)
    metric = compute_retention_metrics(gym, period_start, today)

    at_risk_by_level = {}
    for level in ['early_warning', 'at_risk', 'high_risk', 'critical']:
        count = MemberProfile.objects.filter(
            gym=gym,
            status='active',
            retention_risk_level=level,
        ).count()
        at_risk_by_level[level] = count

    return {
        'period_start': period_start,
        'period_end': today,
        'total_at_risk': metric.total_at_risk,
        'total_contacted': metric.total_contacted,
        'total_returned': metric.total_returned,
        'return_rate': metric.return_rate,
        'at_risk_by_level': at_risk_by_level,
    }