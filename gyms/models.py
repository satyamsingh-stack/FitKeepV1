from django.conf import settings
from django.db import models


class Gym(models.Model):
    """Represents an independent gym tenant."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('cancelled', 'Cancelled'),
        ('trial', 'Trial'),
    ]

    PLAN_CHOICES = [
        ('free', 'Free'),
        ('basic', 'Basic'),
        ('pro', 'Pro'),
        ('enterprise', 'Enterprise'),
    ]

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    timezone = models.CharField(max_length=64, default='Asia/Kolkata')
    currency = models.CharField(max_length=8, default='INR')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')

    trial_ends_at = models.DateTimeField(null=True, blank=True)
    subscription_ends_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Inactivity thresholds (gym-specific overrides)
    inactivity_early_warning_days = models.IntegerField(default=2)
    inactivity_at_risk_days = models.IntegerField(default=3)
    inactivity_high_risk_days = models.IntegerField(default=5)
    inactivity_critical_risk_days = models.IntegerField(default=7)

    # Attendance settings
    attendance_check_in_window_minutes = models.IntegerField(default=120)
    allow_duplicate_daily_checkin = models.BooleanField(default=False)

    # QR code settings
    qr_code_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.name)
            slug = base
            n = 1
            while Gym.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_trial_active(self):
        if self.status == 'trial' and self.trial_ends_at:
            from django.utils import timezone
            return timezone.now() < self.trial_ends_at
        return False

    @property
    def is_subscription_active(self):
        if self.status == 'active' and self.subscription_ends_at:
            from django.utils import timezone
            return timezone.now() < self.subscription_ends_at
        return self.status == 'active'


class GymWorkspaceOTP(models.Model):
    """Server-side OTP challenge for sensitive gym workspace actions."""

    PURPOSE_CREATE_WORKSPACE = 'create_workspace'
    PURPOSE_REGISTER_GYM = 'register_gym'
    PURPOSE_REGISTER_GYM_FITKEEP = 'register_gym_fitkeep'
    PURPOSE_PASSWORD_RESET = 'password_reset'
    PURPOSE_CHOICES = [
        (PURPOSE_CREATE_WORKSPACE, 'Create gym workspace'),
        (PURPOSE_REGISTER_GYM, 'Register gym workspace'),
        (PURPOSE_REGISTER_GYM_FITKEEP, 'FitKeep admin registration verification'),
        (PURPOSE_PASSWORD_RESET, 'Password reset'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='gym_workspace_otps',
        null=True,
        blank=True,
    )
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    email = models.EmailField()
    purpose = models.CharField(max_length=40, choices=PURPOSE_CHOICES, db_index=True)
    code_hash = models.CharField(max_length=128)
    pending_data = models.TextField(help_text='JSON payload stored until verification succeeds.')
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    expires_at = models.DateTimeField(db_index=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'purpose', 'consumed_at']),
            models.Index(fields=['session_key', 'purpose', 'consumed_at']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        target = self.email or (self.user_id and f'user:{self.user_id}') or 'pending'
        return f'Workspace OTP for {target} ({self.purpose})'

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() >= self.expires_at

    @property
    def is_active(self):
        return self.consumed_at is None and not self.is_expired