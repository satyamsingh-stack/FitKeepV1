from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from members.models import MemberProfile


class Payment(models.Model):
    """Records a payment made by a member toward their membership."""

    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('upi', 'UPI'),
        ('card', 'Card'),
        ('bank', 'Bank Transfer'),
        ('cheque', 'Cheque'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='payments')
    subscription = models.ForeignKey(
        'memberships.MemberSubscription', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payments',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    payment_date = models.DateTimeField(default=timezone.now)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
    reference_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    recorded_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payments_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['member', 'payment_date']),
            models.Index(fields=['payment_date']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.member.full_name} - ₹{self.amount} ({self.payment_date.date()})'

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.amount <= 0:
            raise ValidationError('Payment amount must be greater than zero.')


class FeeReminder(models.Model):
    """Tracks fee reminder attempts for members with outstanding balances."""

    member = models.ForeignKey(MemberProfile, on_delete=models.CASCADE, related_name='fee_reminders')
    subscription = models.ForeignKey(
        'memberships.MemberSubscription', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='fee_reminders',
    )
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reminder_date = models.DateTimeField(default=timezone.now)
    method = models.CharField(max_length=20, default='in_app')
    status = models.CharField(max_length=20, default='pending')
    notes = models.TextField(blank=True)
    sent_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['member', 'reminder_date']),
        ]

    def __str__(self):
        return f'Reminder for {self.member.full_name} - ₹{self.amount_due}'