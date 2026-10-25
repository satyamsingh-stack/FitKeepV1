from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from gyms.models import Gym


class User(AbstractUser):
    """Custom user model supporting email/phone login and role-based access."""

    ROLE_CHOICES = [
        ('super_admin', 'Super Admin'),
        ('gym_owner', 'Gym Owner'),
        ('gym_staff', 'Gym Staff'),
        ('gym_member', 'Gym Member'),
    ]

    email = models.EmailField(unique=True, blank=True, null=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='gym_member')
    is_platform_admin = models.BooleanField(default=False)
    date_of_birth = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['phone']),
            models.Index(fields=['role']),
        ]

    def __str__(self):
        return self.username or self.email or self.phone or f'User {self.id}'

    @property
    def is_super_admin(self):
        return self.is_platform_admin or self.is_superuser

    def get_gym(self):
        """Return the primary gym for this user, if any."""
        membership = self.gym_memberships.filter(is_primary=True, status='active').first()
        if membership:
            return membership.gym
        membership = self.gym_memberships.filter(status='active').first()
        return membership.gym if membership else None


class GymMembership(models.Model):
    """Links a user to a gym and stores their role and access status."""

    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('staff', 'Staff'),
        ('member', 'Member'),
    ]

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='gym_memberships',
    )
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    is_primary = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'gym'],
                name='unique_user_gym_membership',
            ),
        ]
        indexes = [
            models.Index(fields=['gym', 'role']),
            models.Index(fields=['user', 'gym']),
        ]

    def __str__(self):
        return f'{self.user} @ {self.gym} ({self.role})'

    def save(self, *args, **kwargs):
        if self.role == 'owner':
            GymMembership.objects.filter(gym=self.gym, role='owner', is_primary=True).exclude(pk=self.pk).update(is_primary=False)
            self.is_primary = True
        super().save(*args, **kwargs)

    @property
    def is_owner(self):
        return self.role == 'owner' and self.status == 'active'

    @property
    def is_staff(self):
        return self.role in ('owner', 'staff') and self.status == 'active'

    @property
    def is_member(self):
        return self.role == 'member' and self.status == 'active'


class GymSettings(models.Model):
    """Gym-specific settings and preferences."""

    gym = models.OneToOneField(Gym, on_delete=models.CASCADE, related_name='settings')

    # Inactivity thresholds
    inactivity_early_warning_days = models.IntegerField(default=2)
    inactivity_at_risk_days = models.IntegerField(default=3)
    inactivity_high_risk_days = models.IntegerField(default=5)
    inactivity_critical_risk_days = models.IntegerField(default=7)

    # Attendance settings
    attendance_check_in_window_minutes = models.IntegerField(default=120)
    allow_duplicate_daily_checkin = models.BooleanField(default=False)
    require_phone_verification = models.BooleanField(default=True)
    allow_manual_checkin = models.BooleanField(default=True)

    # Streak settings
    weekly_attendance_goal = models.IntegerField(default=3, help_text='Target visits per week')
    streak_break_days = models.IntegerField(default=1, help_text='Days before streak breaks')

    # Notification settings
    notify_inactivity = models.BooleanField(default=True)
    notify_expiry = models.BooleanField(default=True)
    notify_payment_reminder = models.BooleanField(default=True)

    # QR settings
    qr_rotation_enabled = models.BooleanField(default=False)
    qr_code_expiry_hours = models.IntegerField(default=24)

    # Membership auto-renewal
    auto_renew_enabled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Gym settings'

    def __str__(self):
        return f'Settings for {self.gym.name}'

    @classmethod
    def get_for_gym(cls, gym):
        settings_obj, _ = cls.objects.get_or_create(gym=gym)
        return settings_obj