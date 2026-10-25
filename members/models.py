from django.db import models

from gyms.models import Gym


class MemberProfile(models.Model):
    """Gym-specific member profile with contact details and status."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('paused', 'Paused'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
    ]

    GENDER_CHOICES = [
        ('', 'Prefer not to say'),
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name='members')
    member_id = models.CharField(max_length=50, db_index=True)
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True)
    emergency_contact = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    photo = models.ImageField(upload_to='member_photos/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    notes = models.TextField(blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Retention tracking
    last_contact_date = models.DateTimeField(null=True, blank=True)
    retention_risk_level = models.CharField(max_length=20, default='none')
    retention_risk_days = models.IntegerField(default=0)
    last_attendance_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'member_id'],
                name='unique_member_id_per_gym',
            ),
        ]
        indexes = [
            models.Index(fields=['gym', 'status']),
            models.Index(fields=['gym', 'phone']),
            models.Index(fields=['gym', 'last_attendance_date']),
            models.Index(fields=['retention_risk_level']),
        ]

    def __str__(self):
        return f'{self.full_name} ({self.member_id})'

    def save(self, *args, **kwargs):
        if not self.member_id:
            self.member_id = self._generate_member_id()
        super().save(*args, **kwargs)

    def _generate_member_id(self):
        from django.utils import timezone
        import random, string
        prefix = 'MBR'
        year = timezone.now().year
        while True:
            suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
            mid = f'{prefix}{year}{suffix}'
            if not MemberProfile.objects.filter(gym=self.gym, member_id=mid).exists():
                return mid

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def has_active_subscription(self):
        return self.subscriptions.filter(status='active').exists()