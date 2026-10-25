from django.db import models
from django.utils import timezone

from gyms.models import Gym
from accounts.models import User


class NotificationType(models.Model):
    """Defines notification categories."""

    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    default_channels = models.CharField(max_length=100, default='in_app')

    def __str__(self):
        return self.name


class Notification(models.Model):
    """In-app notification for gym members and staff."""

    TYPE_CHOICES = [
        ('inactivity_alert', 'Inactivity Alert'),
        ('membership_expiry', 'Membership Expiry'),
        ('payment_reminder', 'Payment Reminder'),
        ('streak_milestone', 'Streak Milestone'),
        ('check_in_confirmation', 'Check-in Confirmation'),
        ('general', 'General'),
    ]

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    related_member_id = models.IntegerField(null=True, blank=True)
    related_subscription_id = models.IntegerField(null=True, blank=True)
    related_attendance_id = models.IntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['gym', 'created_at']),
            models.Index(fields=['notification_type']),
        ]

    def __str__(self):
        return f'{self.recipient} - {self.title}'

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save()

    @classmethod
    def create_notification(cls, gym, recipient, notification_type, title, message, **related_ids):
        return cls.objects.create(
            gym=gym,
            recipient=recipient,
            notification_type=notification_type,
            title=title,
            message=message,
            related_member_id=related_ids.get('member_id'),
            related_subscription_id=related_ids.get('subscription_id'),
            related_attendance_id=related_ids.get('attendance_id'),
        )