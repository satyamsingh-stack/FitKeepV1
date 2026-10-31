from django.db import models
from django.utils import timezone

from gyms.models import Gym
from members.models import MemberProfile


class AttendanceQRCode(models.Model):
    """Gym-specific QR code for attendance check-ins."""

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='qr_codes')
    token_hash = models.CharField(max_length=128, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['gym', 'is_active']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f'QR for {self.gym.name} ({self.id})'

    @property
    def is_valid(self):
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def revoke(self):
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save()

    @classmethod
    def get_active_qr(cls, gym):
        from django.conf import settings
        qr = cls.objects.filter(gym=gym, is_active=True).order_by('-created_at').first()
        if qr and qr.expires_at and timezone.now() > qr.expires_at:
            return None
        return qr

    @classmethod
    def generate_token(cls):
        import secrets
        return secrets.token_urlsafe(32)


class Attendance(models.Model):
    """Records a member's check-in at a gym."""

    SOURCE_CHOICES = [
        ('qr', 'QR Scan'),
        ('manual', 'Manual'),
        ('tablet', 'Tablet'),
    ]

    VERIFICATION_CHOICES = [
        ('verified', 'Verified'),
        ('unverified', 'Unverified'),
        ('otp_verified', 'OTP Verified'),
    ]

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='attendance_records')
    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='attendance_records')
    check_in_at = models.DateTimeField(default=timezone.now)
    check_out_at = models.DateTimeField(null=True, blank=True)
    check_in_date = models.DateField(db_index=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='qr')
    verification_status = models.CharField(max_length=20, choices=VERIFICATION_CHOICES, default='verified')
    verification_method = models.CharField(max_length=50, blank=True)
    recorded_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='manual_attendance_records',
    )
    notes = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['member', 'check_in_date'],
                name='unique_attendance_per_member_per_day',
            ),
        ]
        indexes = [
            models.Index(fields=['gym', 'check_in_date']),
            models.Index(fields=['member', 'check_in_at']),
            models.Index(fields=['check_in_date']),
        ]

    def __str__(self):
        return f'{self.member.full_name} - {self.check_in_at}'

    def _format_gym_time(self, value):
        if not value:
            return '—'
        from attendance.services import get_gym_timezone
        return timezone.localtime(value, get_gym_timezone(self.gym)).strftime('%H:%M')

    @property
    def check_in_time_local(self):
        return self._format_gym_time(self.check_in_at)

    @property
    def check_out_time_local(self):
        return self._format_gym_time(self.check_out_at)

    def save(self, *args, **kwargs):
        if not self.check_in_date:
            tz = self.gym.timezone if self.gym else 'UTC'
            from django.utils.timezone import make_aware
            from datetime import datetime
            import pytz
            try:
                tzinfo = pytz.timezone(tz)
            except Exception:
                tzinfo = pytz.UTC
            local_dt = self.check_in_at.astimezone(tzinfo) if self.check_in_at.tzinfo else self.check_in_at
            self.check_in_date = local_dt.date()
        super().save(*args, **kwargs)

    @property
    def is_manual(self):
        return self.source == 'manual' and self.recorded_by is not None