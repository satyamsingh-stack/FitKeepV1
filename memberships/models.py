from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError

from members.models import MemberProfile


class MembershipPlan(models.Model):
    """Gym-specific membership plan definition."""

    DURATION_CHOICES = [
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('half_yearly', 'Half Yearly'),
        ('annual', 'Annual'),
        ('custom', 'Custom'),
    ]

    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='plans')
    name = models.CharField(max_length=100)
    duration = models.CharField(max_length=20, choices=DURATION_CHOICES)
    duration_days = models.IntegerField(default=30, help_text='Duration in days')
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'name'],
                name='unique_plan_name_per_gym',
            ),
        ]
        indexes = [
            models.Index(fields=['gym', 'is_active']),
        ]

    def __str__(self):
        return f'{self.name} - ₹{self.price}'

    @property
    def duration_label(self):
        return dict(self.DURATION_CHOICES).get(self.duration, self.duration)


class MemberSubscription(models.Model):
    """Tracks a member's active subscription period and payment details."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('paused', 'Paused'),
        ('pending', 'Pending'),
    ]

    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(MembershipPlan, on_delete=models.SET_NULL, null=True, related_name='subscriptions')
    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='subscriptions')

    start_date = models.DateField()
    end_date = models.DateField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    # Renewal tracking
    previous_subscription = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='renewals',
    )
    renewal_count = models.IntegerField(default=0)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['member', 'start_date', 'end_date'],
                name='unique_subscription_period_per_member',
            ),
        ]
        indexes = [
            models.Index(fields=['gym', 'status']),
            models.Index(fields=['end_date']),
            models.Index(fields=['member', 'status']),
        ]

    def __str__(self):
        return f'{self.member.full_name} - {self.plan.name if self.plan else "Custom"} ({self.start_date} to {self.end_date})'

    def save(self, *args, **kwargs):
        if not self.gym_id:
            self.gym = self.member.gym
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not self.member_id or not self.start_date or not self.end_date:
            return
        overlapping = MemberSubscription.objects.filter(
            member_id=self.member_id,
            start_date__lte=self.end_date,
            end_date__gte=self.start_date,
        ).exclude(status='cancelled')
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)
        existing = overlapping.order_by('start_date').first()
        if existing:
            raise ValidationError({
                'start_date': (
                    f'This period overlaps an existing membership '
                    f'({existing.start_date:%d %b %Y} to {existing.end_date:%d %b %Y}).'
                ),
            })

    @property
    def outstanding_balance(self):
        from decimal import Decimal
        from django.db.models import Sum
        paid = self.payments.filter(status='completed').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        return max(self.total_amount - self.discount_amount - paid, Decimal('0.00'))

    @property
    def is_expired(self):
        from django.utils import timezone
        return self.end_date < timezone.now().date()

    @property
    def days_remaining(self):
        from django.utils import timezone
        return (self.end_date - timezone.now().date()).days

    def renew(self, new_end_date, plan=None, total_amount=None, new_start_date=None):
        """Create a renewal subscription."""
        from datetime import timedelta
        new_start_date = new_start_date or (self.end_date + timedelta(days=1))
        from attendance.services import get_gym_local_date
        today = get_gym_local_date(self.gym)
        new_status = 'pending' if new_start_date > today else ('expired' if new_end_date < today else 'active')
        new_sub = MemberSubscription(
            member=self.member,
            plan=plan or self.plan,
            gym=self.gym,
            start_date=new_start_date,
            end_date=new_end_date,
            total_amount=total_amount if total_amount is not None else self.total_amount,
            status=new_status,
            previous_subscription=self,
            renewal_count=self.renewal_count + 1,
        )
        new_sub.full_clean()
        new_sub.save()
        # Do not prematurely expire the current membership when renewing ahead of time.
        if self.end_date < today:
            self.status = 'expired'
            self.save(update_fields=['status', 'updated_at'])
        return new_sub