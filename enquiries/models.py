from django.conf import settings
from django.db import models


class Enquiry(models.Model):
    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('follow_up', 'Follow-up'),
        ('converted', 'Converted'),
        ('closed', 'Closed'),
    ]

    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='enquiries')
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    interested_plan = models.ForeignKey(
        'memberships.MembershipPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enquiries',
    )
    details = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enquiries_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['follow_up_date', '-created_at']
        indexes = [
            models.Index(fields=['gym', 'status']),
            models.Index(fields=['gym', 'follow_up_date']),
        ]

    def __str__(self):
        return f'{self.name} - {self.gym.name}'
