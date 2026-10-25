"""
Notifications service module: notification creation, delivery, preferences.
"""
from django.utils import timezone

from notifications.models import Notification, NotificationType


def create_notification(gym, recipient, notification_type, title, message, **related_ids):
    """
    Create an in-app notification.
    """
    return Notification.create_notification(
        gym=gym,
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        message=message,
        member_id=related_ids.get('member_id'),
        subscription_id=related_ids.get('subscription_id'),
        attendance_id=related_ids.get('attendance_id'),
    )


def notify_inactivity(member, gym, days_inactive, recipients, risk_level, skip_existing=False):
    """
    Notify staff about an inactive member.
    """
    level_labels = {
        'early_warning': 'Early Warning',
        'at_risk': 'At Risk',
        'high_risk': 'High Risk',
        'critical': 'Critical',
    }
    level_label = level_labels[risk_level]

    for recipient in recipients:
        if skip_existing and Notification.objects.filter(
            gym=gym,
            recipient=recipient,
            notification_type='inactivity_alert',
            related_member_id=member.id,
            message__icontains=f'Risk level: {risk_level}.',
        ).exists():
            continue
        create_notification(
            gym=gym,
            recipient=recipient,
            notification_type='inactivity_alert',
            title=f'{level_label} Retention Risk: {member.full_name}',
            message=f'{member.full_name} has been inactive for {days_inactive} days. Risk level: {level_label}.',
            member_id=member.id,
        )


def notify_membership_expiry(subscription, recipients, days_remaining):
    """
    Notify staff about an expiring membership.
    """
    for recipient in recipients:
        create_notification(
            gym=subscription.gym,
            recipient=recipient,
            notification_type='membership_expiry',
            title=f'Membership Expiring: {subscription.member.full_name}',
            message=f'{subscription.member.full_name}\'s membership expires in {days_remaining} days.',
            subscription_id=subscription.id,
        )


def notify_payment_reminder(member, gym, outstanding_amount, recipients):
    """
    Notify staff about outstanding payments.
    """
    for recipient in recipients:
        create_notification(
            gym=gym,
            recipient=recipient,
            notification_type='payment_reminder',
            title=f'Payment Reminder: {member.full_name}',
            message=f'{member.full_name} has an outstanding payment of ₹{outstanding_amount}.',
            member_id=member.id,
        )


def notify_streak_milestone(member, gym, streak, recipients):
    """
    Notify about a streak milestone.
    """
    for recipient in recipients:
        create_notification(
            gym=gym,
            recipient=recipient,
            notification_type='streak_milestone',
            title=f'Streak Milestone: {member.full_name}',
            message=f'{member.full_name} has achieved a {streak}-day attendance streak!',
            member_id=member.id,
        )


def get_unread_count(user):
    """Get count of unread notifications for a user."""
    return Notification.objects.filter(recipient=user, is_read=False).count()


def mark_all_read(user):
    """Mark all notifications as read for a user."""
    return Notification.objects.filter(recipient=user, is_read=False).update(is_read=True, read_at=timezone.now())