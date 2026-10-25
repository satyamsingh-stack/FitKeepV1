from django.db import models
from django.utils import timezone

from accounts.models import User
from gyms.models import Gym


class AuditLog(models.Model):
    """Records important administrative actions for security and compliance."""

    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('password_change', 'Password Change'),
        ('password_reset', 'Password Reset'),
        ('member_checkin', 'Member Check-in'),
        ('payment_recorded', 'Payment Recorded'),
        ('qr_generated', 'QR Generated'),
        ('qr_revoked', 'QR Revoked'),
        ('contact_made', 'Contact Made'),
        ('status_change', 'Status Change'),
        ('export_data', 'Export Data'),
    ]

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='audit_logs', null=True, blank=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    target_model = models.CharField(max_length=100, blank=True)
    target_id = models.IntegerField(null=True, blank=True)
    target_repr = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['actor', 'timestamp']),
            models.Index(fields=['gym', 'timestamp']),
            models.Index(fields=['action']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f'{self.actor} - {self.action} - {self.target_repr}'

    @classmethod
    def log_action(cls, actor, action, gym=None, target=None, metadata=None, ip_address=None, user_agent=None):
        target_model = ''
        target_id = None
        target_repr = ''
        if target:
            target_model = target.__class__.__name__
            target_id = target.pk
            target_repr = str(target)
        return cls.objects.create(
            actor=actor,
            gym=gym,
            action=action,
            target_model=target_model,
            target_id=target_id,
            target_repr=target_repr,
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent or '',
        )