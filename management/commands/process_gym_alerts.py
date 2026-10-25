"""
Management command to process gym alerts: inactivity, expiry, payments.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from gyms.models import Gym
from memberships.models import MemberSubscription
from payments.services import get_outstanding_payments
from notifications.services import (
    notify_membership_expiry,
    notify_payment_reminder,
)
from retention.services import get_at_risk_members
from accounts.models import User


class Command(BaseCommand):
    help = 'Process gym alerts: inactivity, subscription expiry, payment reminders'

    def add_arguments(self, parser):
        parser.add_argument('--gym-id', type=int, default=None, help='Process specific gym only')
        parser.add_argument('--expiry-days', type=int, default=7, help='Days before expiry to trigger alert')

    def handle(self, *args, **options):
        gym_id = options['gym_id']
        expiry_threshold = options['expiry_days']

        gyms = Gym.objects.filter(status='active')
        if gym_id:
            gyms = gyms.filter(id=gym_id)

        for gym in gyms:
            self.stdout.write(f'Processing alerts for {gym.name}...')

            # Get staff recipients for notifications
            staff = User.objects.filter(
                gym_memberships__gym=gym,
                gym_memberships__role__in=['owner', 'staff'],
                gym_memberships__status='active',
            ).distinct()

            if not staff.exists():
                self.stdout.write(self.style.WARNING(f'  No staff found for {gym.name}'))
                continue

            # Refresh risk and create alerts only when a member enters a new risk level.
            get_at_risk_members(gym)

            # Process subscription expiry alerts.
            today = timezone.now().date()
            expiring_soon = today + timedelta(days=expiry_threshold)
            expiring_subs = MemberSubscription.objects.filter(
                gym=gym,
                status='active',
                end_date__gte=today,
                end_date__lte=expiring_soon,
            )

            for sub in expiring_subs:
                days_remaining = (sub.end_date - today).days
                notify_membership_expiry(sub, staff, days_remaining)

            # Process payment reminders.
            outstanding = get_outstanding_payments(gym)
            for item in outstanding:
                notify_payment_reminder(item['member'], gym, item['outstanding'], staff)

            self.stdout.write(self.style.SUCCESS(f'  Processed alerts for {gym.name}'))

        self.stdout.write(self.style.SUCCESS('Alert processing complete!'))