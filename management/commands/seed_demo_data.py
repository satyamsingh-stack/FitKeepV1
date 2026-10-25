"""
Management command to seed demo data for development.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
import random

from gyms.models import Gym
from accounts.models import GymMembership, GymSettings
from members.models import MemberProfile
from memberships.models import MembershipPlan, MemberSubscription
from attendance.models import Attendance
from payments.models import Payment


class Command(BaseCommand):
    help = 'Seed demo data for development and testing'

    def add_arguments(self, parser):
        parser.add_argument('--gym-count', type=int, default=3, help='Number of gyms to create')
        parser.add_argument('--members-per-gym', type=int, default=20, help='Members per gym')

    def handle(self, *args, **options):
        User = get_user_model()
        gym_count = options['gym_count']
        members_per_gym = options['members_per_gym']

        # Create super admin
        super_admin, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@gymplatform.local',
                'role': 'super_admin',
                'is_platform_admin': True,
                'is_superuser': True,
                'is_staff': True,
            }
        )
        super_admin.set_password('admin123')
        super_admin.save()
        self.stdout.write(self.style.SUCCESS(f'Created super admin: {super_admin.username}'))

        # Create gyms
        for i in range(gym_count):
            gym, created = Gym.objects.get_or_create(
                name=f'FitLife Gym {i+1}',
                defaults={
                    'slug': f'fitlife-gym-{i+1}',
                    'status': 'active',
                    'plan': 'pro',
                    'phone': f'+91987654321{i}',
                    'email': f'gym{i+1}@fitlife.local',
                    'address': f'{100+i} Main St, City',
                    'timezone': 'Asia/Kolkata',
                    'currency': 'INR',
                }
            )
            if created:
                GymSettings.objects.get_or_create(gym=gym)
                GymMembership.objects.get_or_create(
                    user=super_admin,
                    gym=gym,
                    defaults={'role': 'owner', 'is_primary': True, 'status': 'active'},
                )

                # Create plans
                plans = []
                for plan_name, duration, duration_days, price in [
                    ('Basic Monthly', 'monthly', 30, 999),
                    ('Premium Quarterly', 'quarterly', 90, 2499),
                    ('Annual', 'annual', 365, 8999),
                ]:
                    plan, _ = MembershipPlan.objects.get_or_create(
                        gym=gym,
                        name=plan_name,
                        defaults={
                            'duration': duration,
                            'duration_days': duration_days,
                            'price': price,
                            'is_active': True,
                        }
                    )
                    plans.append(plan)

                # Create members
                for j in range(members_per_gym):
                    member = MemberProfile.objects.create(
                        gym=gym,
                        full_name=f'Member {i+1}-{j+1}',
                        phone=f'+91987654{100+i+j:04d}',
                        email=f'member{i+1}{j+1}@test.local',
                        status='active',
                        joined_at=timezone.now() - timedelta(days=random.randint(1, 365)),
                    )

                    # Create subscription
                    plan = random.choice(plans)
                    start_date = member.joined_at.date()
                    end_date = start_date + timedelta(days=plan.duration_days)
                    subscription = MemberSubscription.objects.create(
                        member=member,
                        plan=plan,
                        gym=gym,
                        start_date=start_date,
                        end_date=end_date,
                        total_amount=plan.price,
                        status='active',
                    )

                    # Create some attendance records
                    for k in range(random.randint(5, 20)):
                        check_date = start_date + timedelta(days=random.randint(0, min(plan.duration_days - 1, 30)))
                        Attendance.objects.create(
                            gym=gym,
                            member=member,
                            check_in_at=timezone.make_aware(
                                __import__('datetime').datetime.combine(check_date, __import__('datetime').time(random.randint(6, 10), random.randint(0, 59)))
                            ),
                            check_in_date=check_date,
                            source='qr',
                            verification_status='verified',
                        )

                    # Create some payments
                    if random.random() > 0.3:
                        Payment.objects.create(
                            member=member,
                            subscription=subscription,
                            amount=plan.price,
                            method=random.choice(['cash', 'upi', 'card', 'bank']),
                            payment_date=subscription.start_date,
                            status='completed',
                        )

                self.stdout.write(self.style.SUCCESS(f'Created gym: {gym.name} with {members_per_gym} members'))
            else:
                self.stdout.write(self.style.WARNING(f'Gym already exists: {gym.name}'))

        self.stdout.write(self.style.SUCCESS('Demo data seeded successfully!'))