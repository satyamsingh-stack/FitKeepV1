from django.db import models
from django.utils import timezone

from members.models import MemberProfile


class RetentionRiskLevel(models.Model):
    """Defines retention risk categories and thresholds."""

    LEVEL_CHOICES = [
        ('none', 'No Risk'),
        ('early_warning', 'Early Warning'),
        ('at_risk', 'At Risk'),
        ('high_risk', 'High Risk'),
        ('critical', 'Critical Risk'),
    ]

    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='risk_levels')
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    min_days = models.IntegerField(default=0)
    max_days = models.IntegerField(null=True, blank=True)
    action_required = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'level'],
                name='unique_risk_level_per_gym',
            ),
        ]

    def __str__(self):
        return f'{self.gym.name} - {self.level}'

    @classmethod
    def get_risk_level(cls, gym, days_inactive):
        """Determine risk level based on days inactive."""
        level = cls.objects.filter(gym=gym, is_active=True, min_days__lte=days_inactive).order_by('-min_days').first()
        if level:
            if level.max_days and days_inactive > level.max_days:
                # Find next higher level
                next_level = cls.objects.filter(gym=gym, is_active=True, min_days__gt=level.max_days).order_by('min_days').first()
                if next_level:
                    return next_level.level
            return level.level
        return 'none'


class ContactLog(models.Model):
    """Records member contact attempts and outcomes."""

    OUTCOME_CHOICES = [
        ('no_answer', 'No Answer'),
        ('will_return_soon', 'Will Return Soon'),
        ('temporarily_unavailable', 'Temporarily Unavailable'),
        ('cancellation_requested', 'Cancellation Requested'),
        ('returned', 'Returned to Gym'),
        ('other', 'Other'),
    ]

    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='contact_logs')
    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='contact_logs')
    contact_date = models.DateTimeField(default=timezone.now)
    contact_outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, default='other')
    notes = models.TextField(blank=True)
    next_follow_up_date = models.DateField(null=True, blank=True)
    contacted_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='contact_logs_made')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['gym', 'member']),
            models.Index(fields=['contact_date']),
            models.Index(fields=['next_follow_up_date']),
        ]

    def __str__(self):
        return f'{self.member.full_name} - {self.contact_outcome} - {self.contact_date}'


class RetentionMetric(models.Model):
    """Stores computed retention metrics for a gym over a period."""

    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='retention_metrics')
    period_start = models.DateField()
    period_end = models.DateField()
    total_at_risk = models.IntegerField(default=0)
    total_contacted = models.IntegerField(default=0)
    total_returned = models.IntegerField(default=0)
    total_renewed = models.IntegerField(default=0)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'period_start', 'period_end'],
                name='unique_retention_metric_period',
            ),
        ]

    @property
    def return_rate(self):
        if self.total_contacted == 0:
            return 0
        return (self.total_returned / self.total_contacted) * 100

    def __str__(self):
        return f'{self.gym.name} - {self.period_start} to {self.period_end}'