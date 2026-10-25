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