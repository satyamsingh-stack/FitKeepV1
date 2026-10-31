"""
Tests for the Gym Retention & Attendance SaaS platform.
"""
import json
import re

from django.contrib.staticfiles import finders
from django.test import Client, TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from gyms.models import Gym
from gyms.services import get_user_gym, can_access_gym, can_manage_members
from accounts.models import GymMembership
from accounts.models import GymSettings
from members.models import MemberProfile
from memberships.models import MembershipPlan, MemberSubscription
from attendance.models import Attendance
from attendance.services import (
    check_in_member, default_check_out_input, generate_qr_code,
    get_gym_local_date, get_streak_data,
)
from retention.services import calculate_retention_risk, get_at_risk_members
from payments.models import Payment
from payments.services import record_payment, get_outstanding_payments
from notifications.models import Notification
from enquiries.models import Enquiry


User = get_user_model()


class PwaTests(TestCase):
    def test_homepage_links_to_the_pwa_manifest(self):
        response = self.client.get(reverse('landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<link rel="manifest" href="/static/manifest.webmanifest">')

    def test_manifest_declares_android_icon_sizes(self):
        manifest_path = finders.find('manifest.webmanifest')
        with open(manifest_path, encoding='utf-8') as manifest_file:
            manifest = json.load(manifest_file)

        icons = {icon['sizes']: icon for icon in manifest['icons']}
        for size in ('192x192', '512x512'):
            with self.subTest(size=size):
                icon = icons[size]
                self.assertEqual(icon['type'], 'image/png')
                self.assertIsNotNone(finders.find(icon['src'].removeprefix('/static/')))

    def test_service_worker_is_available_at_the_site_root_without_caching(self):
        response = self.client.get(reverse('service_worker'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertContains(response, "const CACHE_NAME = 'fitkeep-pwa-v3';")


class GymServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@test.com',
            password='testpass123',
            role='gym_owner',
        )
        self.gym = Gym.objects.create(
            name='Test Gym',
            slug='test-gym',
            status='active',
        )
        GymMembership.objects.create(
            user=self.user,
            gym=self.gym,
            role='owner',
            is_primary=True,
            status='active',
        )

    def test_get_user_gym(self):
        gym = get_user_gym(self.user)
        self.assertEqual(gym, self.gym)

    def test_can_access_gym(self):
        self.assertTrue(can_access_gym(self.user, self.gym))

    def test_can_manage_members(self):
        self.assertTrue(can_manage_members(self.user, self.gym))


class NotFoundPageTests(TestCase):
    def test_unknown_url_displays_custom_404_page(self):
        response = self.client.get('/login/xyx')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Oops, that page drifted away', status_code=404)
        self.assertContains(response, '404 - Page not found', status_code=404)
        self.assertNotContains(
            response,
            'Using the URLconf defined in config.urls',
            status_code=404,
        )


class CsrfProtectionTests(TestCase):
    def test_login_form_renders_token_accepted_by_csrf_middleware(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse('login'))
        token_match = re.search(
            rb'name="csrfmiddlewaretoken" value="([^"]+)"',
            response.content,
        )

        self.assertIsNotNone(token_match)
        post_response = client.post(reverse('login'), {
            'csrfmiddlewaretoken': token_match.group(1).decode(),
            'username': 'unknown-user',
            'password': 'invalid-password',
        })

        self.assertEqual(post_response.status_code, 200)


class AttendanceServiceTests(TestCase):
    def setUp(self):
        self.gym = Gym.objects.create(name='Test Gym', slug='test-gym', status='active')
        self.member = MemberProfile.objects.create(
            gym=self.gym,
            full_name='Test Member',
            phone='1234567890',
            status='active',
        )

    def test_check_in_member(self):
        result = check_in_member(
            self.member, self.gym,
            check_out_at=timezone.now() + timedelta(hours=2),
            source='qr', verified=True,
        )
        self.assertTrue(result['success'])
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertIsNotNone(Attendance.objects.get().check_out_at)

    def test_check_in_duplicate(self):
        check_out_at = timezone.now() + timedelta(hours=2)
        check_in_member(self.member, self.gym, check_out_at=check_out_at)
        result = check_in_member(self.member, self.gym, check_out_at=check_out_at)
        self.assertFalse(result['success'])
        self.assertIn('Already checked in', result['error'])

    def test_check_in_requires_a_future_end_time(self):
        result = check_in_member(
            self.member,
            self.gym,
            check_out_at=timezone.now() - timedelta(minutes=1),
        )

        self.assertFalse(result['success'])
        self.assertIn('end time later than now', result['error'])
        self.assertFalse(Attendance.objects.exists())

    def test_gym_local_datetime_input_is_converted_to_aware_time(self):
        check_out_value = default_check_out_input(self.gym)

        from attendance.services import parse_gym_datetime
        parsed = parse_gym_datetime(check_out_value, self.gym)

        self.assertIsNotNone(parsed)
        self.assertTrue(timezone.is_aware(parsed))

    def test_get_streak_data(self):
        for i in range(5):
            check_date = timezone.now().date() - timedelta(days=i)
            Attendance.objects.create(
                gym=self.gym,
                member=self.member,
                check_in_date=check_date,
                check_in_at=timezone.now(),
            )
        streak = get_streak_data(self.member)
        self.assertEqual(streak['current_streak'], 5)
        self.assertEqual(streak['longest_streak'], 5)


class MemberQRCheckInTests(TestCase):
    def setUp(self):
        self.gym = Gym.objects.create(name='QR Test Gym', slug='qr-test-gym', status='active')
        self.member = MemberProfile.objects.create(
            gym=self.gym,
            full_name='QR Test Member',
            phone='1234567890',
            status='active',
        )
        self.qr = generate_qr_code(self.gym)
        self.url = reverse('member:check_in')

    def add_active_subscription(self):
        today = get_gym_local_date(self.gym)
        plan = MembershipPlan.objects.create(
            gym=self.gym,
            name='QR Monthly',
            duration='monthly',
            duration_days=30,
            price=999,
        )
        return MemberSubscription.objects.create(
            member=self.member,
            plan=plan,
            gym=self.gym,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30),
            total_amount=999,
            status='active',
        )

    def test_qr_page_is_available_without_member_login(self):
        response = self.client.get(self.url, {'qr': self.qr.token_hash})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registered phone number')

    def test_registered_phone_check_in_returns_attendance_summary(self):
        self.add_active_subscription()

        response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'phone': self.member.phone,
            'check_out_at': default_check_out_input(self.gym),
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(response.json()['today_status'], 'Checked in')
        self.assertEqual(response.json()['current_streak'], 1)
        self.assertEqual(response.json()['monthly_visits'], 1)
        attendance = Attendance.objects.get(gym=self.gym, member=self.member)
        self.assertEqual(attendance.verification_status, 'unverified')
        self.assertEqual(attendance.verification_method, 'registered_phone')
        self.assertIsNotNone(attendance.check_out_at)

    def test_qr_check_in_requires_end_time(self):
        self.add_active_subscription()

        response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'phone': self.member.phone,
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertFalse(Attendance.objects.exists())

    def test_duplicate_check_in_opens_calendar_and_streak_summary(self):
        self.add_active_subscription()
        first_response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'phone': self.member.phone,
            'check_out_at': default_check_out_input(self.gym),
        })
        self.assertTrue(first_response.json()['success'])

        response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'phone': self.member.phone,
            'check_out_at': default_check_out_input(self.gym),
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['already_checked_in'])
        self.assertEqual(response.json()['summary_url'], reverse('member:check_in_summary'))
        self.assertEqual(Attendance.objects.filter(member=self.member).count(), 1)

        summary_response = self.client.get(response.json()['summary_url'])

        self.assertEqual(summary_response.status_code, 200)
        self.assertContains(summary_response, 'Current streak')
        self.assertContains(summary_response, 'Longest streak')
        self.assertContains(summary_response, 'recorded days')
        self.assertContains(summary_response, str(get_gym_local_date(self.gym).day))

    def test_check_in_rejects_missing_or_expired_membership(self):
        today = get_gym_local_date(self.gym)
        plan = MembershipPlan.objects.create(
            gym=self.gym,
            name='Expired QR Monthly',
            duration='monthly',
            duration_days=30,
            price=999,
        )
        MemberSubscription.objects.create(
            member=self.member,
            plan=plan,
            gym=self.gym,
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1),
            total_amount=999,
            status='expired',
        )

        response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'phone': self.member.phone,
            'check_out_at': default_check_out_input(self.gym),
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['success'])
        self.assertEqual(Attendance.objects.count(), 0)

    def test_check_in_rejects_member_without_a_subscription(self):
        response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'phone': self.member.phone,
            'check_out_at': default_check_out_input(self.gym),
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['success'])
        self.assertEqual(Attendance.objects.count(), 0)

    def test_signed_in_member_can_check_in_without_reentering_phone(self):
        self.add_active_subscription()
        user = User.objects.create_user(
            username='qr-member',
            password='testpass123',
            phone=self.member.phone,
            role='gym_member',
        )
        GymMembership.objects.create(user=user, gym=self.gym, role='member', status='active')
        self.client.force_login(user)

        response = self.client.post(self.url, {
            'qr_token': self.qr.token_hash,
            'check_out_at': default_check_out_input(self.gym),
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        attendance = Attendance.objects.get(gym=self.gym, member=self.member)
        self.assertEqual(attendance.verification_status, 'verified')
        self.assertEqual(attendance.verification_method, 'member_account')


class RetentionServiceTests(TestCase):
    def setUp(self):
        self.gym = Gym.objects.create(name='Test Gym', slug='test-gym', status='active')
        self.owner = User.objects.create_user(
            username='retention-owner',
            email='retention-owner@test.com',
            password='pass12345',
            role='gym_owner',
        )
        GymMembership.objects.create(user=self.owner, gym=self.gym, role='owner', status='active')
        self.member = MemberProfile.objects.create(
            gym=self.gym,
            full_name='Test Member',
            phone='1234567890',
            status='active',
            last_attendance_date=timezone.now() - timedelta(days=10),
        )
        # Create risk levels for the gym
        from retention.models import RetentionRiskLevel
        RetentionRiskLevel.objects.create(
            gym=self.gym,
            level='none',
            min_days=0,
            max_days=1,
            is_active=True,
        )
        RetentionRiskLevel.objects.create(
            gym=self.gym,
            level='early_warning',
            min_days=2,
            max_days=2,
            is_active=True,
        )
        RetentionRiskLevel.objects.create(
            gym=self.gym,
            level='at_risk',
            min_days=3,
            max_days=4,
            is_active=True,
        )
        RetentionRiskLevel.objects.create(
            gym=self.gym,
            level='high_risk',
            min_days=5,
            max_days=6,
            is_active=True,
        )
        RetentionRiskLevel.objects.create(
            gym=self.gym,
            level='critical',
            min_days=7,
            is_active=True,
        )

    def test_calculate_retention_risk(self):
        risk_level, days_inactive = calculate_retention_risk(self.member, self.gym)
        self.assertEqual(days_inactive, 10)
        self.assertEqual(risk_level, 'critical')
        notification = Notification.objects.get(recipient=self.owner)
        self.assertIn('Critical', notification.title)
        self.assertIn('Risk level: Critical', notification.message)

        calculate_retention_risk(self.member, self.gym)
        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 1)

    def test_saved_gym_settings_control_risk_thresholds(self):
        from retention.models import RetentionRiskLevel

        RetentionRiskLevel.objects.filter(gym=self.gym).delete()
        self.gym.inactivity_critical_risk_days = 30
        self.gym.save(update_fields=['inactivity_critical_risk_days'])
        self.member.last_attendance_date = timezone.now() - timedelta(days=6)
        self.member.save(update_fields=['last_attendance_date'])
        GymSettings.objects.create(
            gym=self.gym,
            inactivity_early_warning_days=1,
            inactivity_at_risk_days=2,
            inactivity_high_risk_days=3,
            inactivity_critical_risk_days=5,
        )

        risk_level, _ = calculate_retention_risk(self.member, self.gym)

        self.assertEqual(risk_level, 'critical')

    def test_existing_critical_risk_without_notification_is_backfilled(self):
        self.member.retention_risk_level = 'critical'
        self.member.save(update_fields=['retention_risk_level'])

        calculate_retention_risk(self.member, self.gym)

        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 1)

    def test_member_without_attendance_uses_active_membership_start_date(self):
        today = get_gym_local_date(self.gym)
        plan = MembershipPlan.objects.create(
            gym=self.gym,
            name='Risk Test Monthly',
            duration='monthly',
            duration_days=30,
            price=1000,
        )
        self.member.last_attendance_date = None
        self.member.save(update_fields=['last_attendance_date'])
        MemberSubscription.objects.create(
            member=self.member,
            plan=plan,
            gym=self.gym,
            start_date=today - timedelta(days=27),
            end_date=today + timedelta(days=2),
            total_amount=1000,
            status='active',
        )

        risk_level, days_inactive = calculate_retention_risk(self.member, self.gym)

        self.assertEqual(days_inactive, 27)
        self.assertEqual(risk_level, 'critical')
        notification = Notification.objects.get(recipient=self.owner)
        self.assertIn('Critical', notification.title)

    def test_notification_icon_shows_unread_count(self):
        Notification.objects.create(
            gym=self.gym,
            recipient=self.owner,
            notification_type='inactivity_alert',
            title='Critical Retention Risk',
            message='Member is at critical risk.',
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse('notification_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="position-absolute top-0 start-100 translate-middle badge rounded-pill bg-danger">1</span>')


class PaymentServiceTests(TestCase):
    def setUp(self):
        self.gym = Gym.objects.create(name='Test Gym', slug='test-gym', status='active')
        self.owner = User.objects.create_user(
            username='payment-owner',
            email='payment-owner@test.com',
            password='pass12345',
            role='gym_owner',
        )
        GymMembership.objects.create(user=self.owner, gym=self.gym, role='owner', status='active')
        self.member = MemberProfile.objects.create(
            gym=self.gym,
            full_name='Test Member',
            phone='1234567890',
            status='active',
        )
        self.plan = MembershipPlan.objects.create(
            gym=self.gym,
            name='Monthly',
            duration='monthly',
            duration_days=30,
            price=999,
        )
        self.subscription = MemberSubscription.objects.create(
            member=self.member,
            plan=self.plan,
            gym=self.gym,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30),
            total_amount=999,
            status='active',
        )

    def test_record_payment(self):
        payment = record_payment(self.member, 500, method='cash', subscription=self.subscription)
        self.assertEqual(payment.amount, 500)
        self.assertEqual(payment.status, 'completed')

    def test_record_payment_preserves_backdated_local_date(self):
        payment_date = get_gym_local_date(self.gym) - timedelta(days=12)

        payment = record_payment(
            self.member,
            500,
            subscription=self.subscription,
            payment_date=payment_date,
        )

        self.assertEqual(get_gym_local_date(self.gym, payment.payment_date), payment_date)
        self.assertEqual(self.subscription.payments.get().amount, 500)

    def test_record_payment_rejects_future_date(self):
        with self.assertRaises(ValidationError):
            record_payment(
                self.member,
                500,
                subscription=self.subscription,
                payment_date=get_gym_local_date(self.gym) + timedelta(days=1),
            )

    def test_payment_form_records_selected_backdate(self):
        payment_date = get_gym_local_date(self.gym) - timedelta(days=8)
        self.client.force_login(self.owner)

        response = self.client.post(reverse('payment_add'), {
            'member_id': self.member.member_id,
            'subscription_id': str(self.subscription.id),
            'amount': '125.50',
            'method': 'cash',
            'payment_date': payment_date.isoformat(),
        })

        self.assertRedirects(response, reverse('payment_list'))
        payment = Payment.objects.get(member=self.member)
        self.assertEqual(get_gym_local_date(self.gym, payment.payment_date), payment_date)

    def test_get_outstanding_payments(self):
        record_payment(self.member, 500, subscription=self.subscription)
        outstanding = get_outstanding_payments(self.gym)
        self.assertEqual(len(outstanding), 1)
        self.assertEqual(outstanding[0]['outstanding'], 499)


class EnquiryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='enquiry-owner',
            email='enquiry-owner@test.com',
            password='pass12345',
            role='gym_owner',
        )
        self.gym = Gym.objects.create(
            name='Enquiry Gym',
            slug='enquiry-gym',
            status='active',
            phone='+91 98765 43210',
        )
        GymMembership.objects.create(
            user=self.owner,
            gym=self.gym,
            role='owner',
            is_primary=True,
            status='active',
        )
        self.plan = MembershipPlan.objects.create(
            gym=self.gym,
            name='Strength Plan',
            duration='monthly',
            duration_days=30,
            price=1200,
            description='Weights and coaching.',
        )
        self.client.force_login(self.owner)

    def test_owner_can_add_enquiry_with_name_phone_and_comment(self):
        response = self.client.post(reverse('enquiry_list'), {
            'name': 'Asha Rao',
            'phone': '9876543210',
            'comments': 'Asked about strength training.',
        })

        self.assertRedirects(response, reverse('enquiry_list'))
        enquiry = Enquiry.objects.get(gym=self.gym)
        self.assertEqual(enquiry.created_by, self.owner)
        self.assertEqual(enquiry.name, 'Asha Rao')
        self.assertEqual(enquiry.phone, '9876543210')
        self.assertEqual(enquiry.comments, 'Asked about strength training.')
        self.assertEqual(enquiry.status, 'new')
        self.assertIsNone(enquiry.follow_up_date)

    def test_enquiry_form_only_offers_name_phone_and_comment(self):
        response = self.client.get(reverse('enquiry_list'))

        self.assertEqual(set(response.context['form'].fields), {'name', 'phone', 'comments'})
        self.assertContains(response, 'Phone no')
        self.assertContains(response, '>Comment</label>')
        self.assertNotContains(response, 'WhatsApp number</label>')
        self.assertNotContains(response, 'Email (optional)</label>')
        self.assertNotContains(response, 'Interested plan</label>')
        self.assertNotContains(response, 'Follow-up date</label>')
        self.assertNotContains(response, 'Status</label>')
        self.assertNotContains(response, '<th>Follow-up</th>')
        self.assertNotContains(response, '<th>Status</th>')

    def test_enquiry_page_offers_prefilled_welcome_whatsapp_only(self):
        Enquiry.objects.create(
            gym=self.gym,
            name='Asha Rao',
            phone='+91 98765 43210',
        )

        response = self.client.get(reverse('enquiry_list'))

        self.assertContains(response, 'https://wa.me/919876543210?text=')
        self.assertContains(response, 'WhatsApp welcome')
        self.assertNotContains(response, 'Membership brochure')
        self.assertNotContains(response, 'Send plans brochure')

    def test_editing_enquiry_updates_only_name_phone_and_comment(self):
        enquiry = Enquiry.objects.create(
            gym=self.gym,
            name='Asha Rao',
            phone='9876543210',
            details='Legacy enquiry details',
            comments='Old comment',
            interested_plan=self.plan,
            follow_up_date=get_gym_local_date(self.gym) + timedelta(days=2),
            status='follow_up',
        )

        response = self.client.post(reverse('enquiry_list'), {
            'enquiry_id': enquiry.id,
            'name': 'Asha Sharma',
            'phone': '9999999999',
            'comments': 'New comment',
        })

        self.assertRedirects(response, reverse('enquiry_list'))
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.name, 'Asha Sharma')
        self.assertEqual(enquiry.phone, '9999999999')
        self.assertEqual(enquiry.comments, 'New comment')
        self.assertEqual(enquiry.details, 'Legacy enquiry details')
        self.assertEqual(enquiry.interested_plan, self.plan)
        self.assertEqual(enquiry.status, 'follow_up')


class ModelTests(TestCase):
    def test_gym_slug_auto_generated(self):
        gym = Gym.objects.create(name='My New Gym')
        self.assertEqual(gym.slug, 'my-new-gym')

    def test_member_id_auto_generated(self):
        gym = Gym.objects.create(name='Test Gym', slug='test-gym')
        member = MemberProfile.objects.create(
            gym=gym,
            full_name='Test Member',
            phone='1234567890',
        )
        self.assertTrue(member.member_id.startswith('MBR'))

    def test_subscription_outstanding_balance(self):
        gym = Gym.objects.create(name='Test Gym', slug='test-gym')
        member = MemberProfile.objects.create(
            gym=gym,
            full_name='Test Member',
            phone='1234567890',
        )
        plan = MembershipPlan.objects.create(
            gym=gym,
            name='Monthly',
            duration='monthly',
            duration_days=30,
            price=1000,
        )
        subscription = MemberSubscription.objects.create(
            member=member,
            plan=plan,
            gym=gym,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30),
            total_amount=1000,
            status='active',
        )
        self.assertEqual(subscription.outstanding_balance, 1000)


class MemberOnboardingTests(TestCase):
    """Adding a member should also assign the membership and record the first payment."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', email='owner@test.com', password='pass12345', role='gym_owner',
        )
        self.gym = Gym.objects.create(name='Onboard Gym', slug='onboard-gym', status='active')
        GymMembership.objects.create(user=self.owner, gym=self.gym, role='owner', is_primary=True, status='active')
        self.plan = MembershipPlan.objects.create(
            gym=self.gym, name='Monthly', duration='monthly', duration_days=30, price=1500,
        )
        self.client.login(username='owner', password='pass12345')
        self.today = get_gym_local_date(self.gym)

    def _post(self, **overrides):
        data = {
            'full_name': 'Priya Sharma',
            'phone': '9876543210',
            'plan': str(self.plan.id),
            'start_date': self.today.isoformat(),
            'discount_amount': '0',
            'amount_paid': '0',
            'payment_method': 'cash',
        }
        data.update(overrides)
        return self.client.post(reverse('member_add'), data)

    def test_onboarding_creates_member_membership_and_payment(self):
        response = self._post(amount_paid='1500', discount_amount='0')
        member = MemberProfile.objects.get(gym=self.gym, phone='9876543210')
        self.assertRedirects(response, reverse('member_detail', args=[member.member_id]))
        sub = member.subscriptions.get()
        self.assertEqual(sub.plan, self.plan)
        self.assertEqual(sub.total_amount, 1500)
        self.assertEqual(sub.start_date, self.today)
        self.assertEqual(sub.end_date, self.today + timedelta(days=29))
        self.assertEqual(sub.status, 'active')
        self.assertEqual(member.payments.count(), 1)
        self.assertEqual(sub.outstanding_balance, 0)

    def test_onboarding_rejects_email_already_used_by_another_account(self):
        response = self._post(email='OWNER@Test.com')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already linked to another account')
        self.assertContains(response, 'class="form-control is-invalid"')
        self.assertFalse(MemberProfile.objects.filter(gym=self.gym, phone='9876543210').exists())
        self.assertFalse(User.objects.filter(username='9876543210').exists())

    def test_partial_payment_leaves_balance_outstanding(self):
        self._post(amount_paid='500', discount_amount='100')
        sub = MemberSubscription.objects.get(member__phone='9876543210')
        self.assertEqual(sub.outstanding_balance, 900)

    def test_pay_later_records_no_payment(self):
        self._post(amount_paid='0')
        member = MemberProfile.objects.get(phone='9876543210')
        self.assertEqual(member.payments.count(), 0)
        self.assertEqual(member.subscriptions.get().outstanding_balance, 1500)

    def test_member_can_still_be_added_without_membership(self):
        self._post(plan='', start_date='', amount_paid='0')
        member = MemberProfile.objects.get(phone='9876543210')
        self.assertEqual(member.subscriptions.count(), 0)

    def test_custom_membership_without_plan(self):
        end = self.today + timedelta(days=10)
        self._post(plan='', total_amount='800', end_date=end.isoformat(), amount_paid='800')
        sub = MemberSubscription.objects.get(member__phone='9876543210')
        self.assertIsNone(sub.plan)
        self.assertEqual(sub.end_date, end)
        self.assertEqual(sub.outstanding_balance, 0)

    def test_future_start_date_is_pending(self):
        future = self.today + timedelta(days=5)
        self._post(start_date=future.isoformat())
        self.assertEqual(MemberSubscription.objects.get(member__phone='9876543210').status, 'pending')

    def test_overpayment_is_rejected_and_nothing_is_saved(self):
        response = self._post(amount_paid='2000')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MemberProfile.objects.filter(phone='9876543210').exists())

    def test_payment_without_membership_is_rejected(self):
        response = self._post(plan='', start_date='', amount_paid='500')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MemberProfile.objects.filter(phone='9876543210').exists())

    def test_duplicate_phone_in_same_gym_is_rejected(self):
        self._post()
        response = self._post(full_name='Someone Else')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MemberProfile.objects.filter(gym=self.gym, phone='9876543210').count(), 1)

    def test_other_gyms_plan_cannot_be_used(self):
        other = Gym.objects.create(name='Other Gym', slug='other-gym', status='active')
        foreign_plan = MembershipPlan.objects.create(
            gym=other, name='Foreign', duration='monthly', duration_days=30, price=1,
        )
        response = self._post(plan=str(foreign_plan.id))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MemberProfile.objects.filter(phone='9876543210').exists())

    def test_onboarded_member_can_log_in_to_member_portal(self):
        self._post()
        self.assertTrue(User.objects.filter(username='9876543210', role='gym_member').exists())



class AdminWorkflowTests(TestCase):
    """Day-to-day admin shortcuts: auto status sync, renew+pay, filters, one-click actions."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='boss', email='boss@test.com', password='pass12345', role='gym_owner',
        )
        self.gym = Gym.objects.create(name='Flow Gym', slug='flow-gym', status='active')
        GymMembership.objects.create(user=self.owner, gym=self.gym, role='owner', is_primary=True, status='active')
        self.plan = MembershipPlan.objects.create(
            gym=self.gym, name='Monthly', duration='monthly', duration_days=30, price=1000,
        )
        self.member = MemberProfile.objects.create(gym=self.gym, full_name='Ravi Kumar', phone='9000000001', status='active')
        self.today = get_gym_local_date(self.gym)
        self.client.login(username='boss', password='pass12345')

    def _sub(self, start, end, status, total=1000):
        return MemberSubscription.objects.create(
            member=self.member, plan=self.plan, gym=self.gym,
            start_date=start, end_date=end, total_amount=total, status=status,
        )

    # --- automatic status handling -------------------------------------------------
    def test_finished_memberships_expire_automatically(self):
        from memberships.services import sync_subscription_statuses
        sub = self._sub(self.today - timedelta(days=40), self.today - timedelta(days=10), 'active')
        sync_subscription_statuses(gym=self.gym)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'expired')
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, 'inactive')

    def test_expired_membership_does_not_deactivate_member_with_current_membership(self):
        from memberships.services import sync_subscription_statuses
        self._sub(self.today - timedelta(days=40), self.today - timedelta(days=10), 'active')
        self._sub(self.today - timedelta(days=5), self.today + timedelta(days=25), 'active')

        sync_subscription_statuses(gym=self.gym)

        self.member.refresh_from_db()
        self.assertEqual(self.member.status, 'active')

    def test_member_directory_shows_inactive_after_membership_expires(self):
        self._sub(self.today - timedelta(days=40), self.today - timedelta(days=10), 'expired')

        response = self.client.get(reverse('member_list'))

        self.assertEqual(response.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, 'inactive')
        self.assertContains(response, '>inactive</span>')

    def test_member_detail_shows_inactive_after_membership_expires(self):
        self._sub(self.today - timedelta(days=600), self.today - timedelta(days=500), 'expired')

        response = self.client.get(reverse('member_detail', args=[self.member.member_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<strong>Status:</strong> <span class="badge bg-secondary">inactive</span>')
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, 'inactive')

    def test_renewal_reactivates_member_deactivated_by_expired_membership(self):
        from memberships.services import sync_subscription_statuses
        expired = self._sub(self.today - timedelta(days=40), self.today - timedelta(days=10), 'active')
        sync_subscription_statuses(gym=self.gym)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, 'inactive')

        response = self.client.post(reverse('subscription_renew', args=[expired.id]), {
            'total_amount': '1000',
            'discount_amount': '0',
            'amount_paid': '0',
            'payment_method': 'cash',
        })

        self.assertRedirects(response, reverse('member_detail', args=[self.member.member_id]))
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, 'active')

    def test_pending_membership_activates_when_it_starts(self):
        from memberships.services import sync_subscription_statuses
        sub = self._sub(self.today, self.today + timedelta(days=29), 'pending')
        sync_subscription_statuses(gym=self.gym)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'active')

    def test_paused_and_cancelled_are_never_touched(self):
        from memberships.services import sync_subscription_statuses
        paused = self._sub(self.today - timedelta(days=5), self.today + timedelta(days=5), 'paused')
        sync_subscription_statuses(gym=self.gym)
        paused.refresh_from_db()
        self.assertEqual(paused.status, 'paused')

    def test_member_can_check_in_when_pending_membership_starts_today(self):
        self._sub(self.today, self.today + timedelta(days=29), 'pending')
        result = check_in_member(
            self.member, self.gym,
            check_out_at=timezone.now() + timedelta(hours=2),
            source='manual', verified=True, recorded_by=self.owner,
        )
        self.assertTrue(result['success'])

    def test_front_desk_check_in_stores_selected_end_time(self):
        end_time = default_check_out_input(self.gym)

        response = self.client.post(reverse('check_in'), {
            'phone': self.member.phone,
            'check_out_at': end_time,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        attendance = Attendance.objects.get(member=self.member)
        self.assertIsNotNone(attendance.check_out_at)
        self.assertIn('recorded until', response.json()['message'])

    def test_brand_logo_links_to_overview_for_authenticated_gym_users(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'class="navbar-brand brand-mark" href="{reverse("dashboard")}"',
        )

    # --- renew + pay ---------------------------------------------------------------
    def test_renewal_with_payment_in_one_step(self):
        sub = self._sub(self.today - timedelta(days=30), self.today - timedelta(days=1), 'active')
        response = self.client.post(reverse('subscription_renew', args=[sub.id]), {
            'total_amount': '1000', 'discount_amount': '100', 'amount_paid': '400', 'payment_method': 'upi',
        })
        self.assertRedirects(response, reverse('member_detail', args=[self.member.member_id]))
        new_sub = MemberSubscription.objects.get(previous_subscription=sub)
        self.assertEqual(new_sub.start_date, self.today)
        self.assertEqual(new_sub.end_date, self.today + timedelta(days=29))
        self.assertEqual(new_sub.discount_amount, 100)
        self.assertEqual(new_sub.outstanding_balance, 500)
        self.assertEqual(self.member.payments.get().method, 'upi')
        self.assertEqual(new_sub.plan, self.plan)

    def test_renewal_rejects_a_period_overlapping_another_membership(self):
        expired = self._sub(self.today - timedelta(days=30), self.today - timedelta(days=1), 'expired')
        self._sub(self.today, self.today + timedelta(days=10), 'active')

        response = self.client.post(reverse('subscription_renew', args=[expired.id]), {
            'total_amount': '1000',
            'discount_amount': '0',
            'amount_paid': '0',
            'payment_method': 'cash',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'overlaps an existing membership')
        self.assertEqual(MemberSubscription.objects.filter(member=self.member).count(), 2)


    def test_renewal_form_offers_plans_and_dates_with_current_plan_defaults(self):
        sub = self._sub(self.today - timedelta(days=30), self.today - timedelta(days=1), 'expired', total=850)

        response = self.client.get(reverse('subscription_renew', args=[sub.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="plan"')
        self.assertContains(response, 'name="start_date"')
        self.assertContains(response, 'name="end_date"')
        self.assertContains(response, f'value="{self.plan.id}" selected')
        self.assertContains(response, f'value="{self.today.isoformat()}"')
        self.assertContains(response, 'value="850.00"')

    def test_renewal_page_shows_member_membership_and_payment_history(self):
        previous = self._sub(
            self.today - timedelta(days=90),
            self.today - timedelta(days=61),
            'expired',
            total=900,
        )
        current = self._sub(
            self.today - timedelta(days=30),
            self.today - timedelta(days=1),
            'expired',
            total=1000,
        )
        previous_payment_date = timezone.now() - timedelta(days=80)
        Payment.objects.create(
            member=self.member,
            subscription=previous,
            amount=450,
            payment_date=previous_payment_date,
            method='upi',
            reference_number='OLD-REF-01',
            status='completed',
        )

        response = self.client.get(reverse('subscription_renew', args=[current.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'{previous.start_date:%d %b %Y} – {previous.end_date:%d %b %Y}',
        )
        self.assertContains(response, '900.00')
        self.assertContains(response, '450.00')
        self.assertContains(response, f'{previous_payment_date:%d %b %Y}')
        self.assertContains(response, 'OLD-REF-01')
        self.assertEqual(response.context['membership_history'].count(), 2)
        self.assertEqual(response.context['payment_history'].count(), 1)

    def test_subscription_assignment_rejects_overlapping_membership_dates(self):
        existing = self._sub(self.today, self.today + timedelta(days=29), 'active')

        response = self.client.post(reverse('subscription_add'), {
            'member': str(self.member.pk),
            'plan': str(self.plan.pk),
            'start_date': self.today.isoformat(),
            'end_date': (self.today + timedelta(days=29)).isoformat(),
            'total_amount': '1000',
            'discount_amount': '0',
            'notes': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'overlaps an existing membership')
        self.assertEqual(MemberSubscription.objects.filter(member=self.member).count(), 1)
        self.assertTrue(MemberSubscription.objects.filter(pk=existing.pk).exists())

    def test_subscription_assignment_allows_back_to_back_memberships(self):
        self._sub(self.today, self.today + timedelta(days=29), 'active')
        next_start = self.today + timedelta(days=30)

        response = self.client.post(reverse('subscription_add'), {
            'member': str(self.member.pk),
            'plan': str(self.plan.pk),
            'start_date': next_start.isoformat(),
            'end_date': (next_start + timedelta(days=29)).isoformat(),
            'total_amount': '1000',
            'discount_amount': '0',
            'notes': '',
        })

        self.assertRedirects(response, reverse('subscription_list'))
        self.assertEqual(MemberSubscription.objects.filter(member=self.member).count(), 2)

    def test_renewal_can_select_another_plan_and_custom_dates(self):
        sub = self._sub(self.today - timedelta(days=30), self.today - timedelta(days=1), 'expired')
        alternate_plan = MembershipPlan.objects.create(
            gym=self.gym, name='Quarterly', duration='quarterly', duration_days=90, price=2500,
        )
        new_start = self.today + timedelta(days=3)
        new_end = new_start + timedelta(days=119)

        response = self.client.post(reverse('subscription_renew', args=[sub.id]), {
            'plan': str(alternate_plan.id),
            'start_date': new_start.isoformat(),
            'end_date': new_end.isoformat(),
            'total_amount': '2500',
            'discount_amount': '100',
            'amount_paid': '400',
            'payment_method': 'upi',
        })

        self.assertRedirects(response, reverse('member_detail', args=[self.member.member_id]))
        renewal = MemberSubscription.objects.get(previous_subscription=sub)
        self.assertEqual(renewal.plan, alternate_plan)
        self.assertEqual(renewal.start_date, new_start)
        self.assertEqual(renewal.end_date, new_end)
        self.assertEqual(renewal.total_amount, 2500)

    def test_renewal_rejects_end_date_before_start_date(self):
        sub = self._sub(self.today - timedelta(days=30), self.today - timedelta(days=1), 'expired')

        response = self.client.post(reverse('subscription_renew', args=[sub.id]), {
            'plan': str(self.plan.id),
            'start_date': self.today.isoformat(),
            'end_date': (self.today - timedelta(days=1)).isoformat(),
            'total_amount': '1000',
            'discount_amount': '0',
            'amount_paid': '0',
            'payment_method': 'cash',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'End date must be on or after the start date.')
        self.assertFalse(MemberSubscription.objects.filter(previous_subscription=sub).exists())

    def test_membership_can_be_edited_without_changing_payment_history(self):
        from payments.models import Payment

        sub = self._sub(self.today - timedelta(days=10), self.today + timedelta(days=20), 'active')
        payment = record_payment(self.member, 200, subscription=sub)
        alternate_plan = MembershipPlan.objects.create(
            gym=self.gym, name='Quarterly', duration='quarterly', duration_days=90, price=2500,
        )
        new_start = self.today - timedelta(days=5)
        new_end = self.today + timedelta(days=84)

        response = self.client.post(reverse('subscription_edit', args=[sub.id]), {
            'plan': str(alternate_plan.id),
            'start_date': new_start.isoformat(),
            'end_date': new_end.isoformat(),
            'total_amount': '2500.00',
            'discount_amount': '100.00',
            'status': 'active',
            'notes': 'Plan updated after discussion.',
        })

        self.assertRedirects(response, reverse('subscription_list'))
        sub.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(sub.plan, alternate_plan)
        self.assertEqual(sub.start_date, new_start)
        self.assertEqual(sub.end_date, new_end)
        self.assertEqual(sub.total_amount, 2500)
        self.assertEqual(sub.discount_amount, 100)
        self.assertEqual(sub.notes, 'Plan updated after discussion.')
        self.assertEqual(payment.amount, 200)

    def test_membership_edit_rejects_plan_from_another_gym(self):
        sub = self._sub(self.today - timedelta(days=10), self.today + timedelta(days=20), 'active')
        other_gym = Gym.objects.create(name='Other Gym', slug='other-membership-gym')
        other_plan = MembershipPlan.objects.create(
            gym=other_gym, name='Other Plan', duration='monthly', duration_days=30, price=500,
        )

        response = self.client.post(reverse('subscription_edit', args=[sub.id]), {
            'plan': str(other_plan.id),
            'start_date': sub.start_date.isoformat(),
            'end_date': sub.end_date.isoformat(),
            'total_amount': str(sub.total_amount),
            'discount_amount': str(sub.discount_amount),
            'status': sub.status,
            'notes': '',
        })

        self.assertEqual(response.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.plan)

    def test_renewal_rejects_overpayment(self):
        sub = self._sub(self.today - timedelta(days=30), self.today - timedelta(days=1), 'active')
        self.client.post(reverse('subscription_renew', args=[sub.id]), {
            'total_amount': '1000', 'discount_amount': '0', 'amount_paid': '5000',
        })
        self.assertFalse(MemberSubscription.objects.filter(previous_subscription=sub).exists())
        self.assertEqual(self.member.payments.count(), 0)

    # --- plans ---------------------------------------------------------------------
    def test_plan_can_be_edited(self):
        response = self.client.post(reverse('plan_edit', args=[self.plan.id]), {
            'name': 'Monthly', 'duration': 'monthly', 'duration_days': '30', 'price': '1200', 'is_active': 'on',
        })
        self.assertRedirects(response, reverse('plan_list'))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price, 1200)

    def test_plan_of_another_gym_cannot_be_edited(self):
        other = Gym.objects.create(name='Other', slug='other-flow', status='active')
        foreign = MembershipPlan.objects.create(gym=other, name='X', duration='monthly', duration_days=30, price=5)
        response = self.client.get(reverse('plan_edit', args=[foreign.id]))
        self.assertEqual(response.status_code, 404)

    # --- one-click actions ---------------------------------------------------------
    def test_mark_present_from_member_page(self):
        self._sub(self.today - timedelta(days=1), self.today + timedelta(days=28), 'active')
        response = self.client.post(reverse('member_check_in', args=[self.member.member_id]))

        self.assertRedirects(response, reverse('member_detail', args=[self.member.member_id]))
        self.assertTrue(Attendance.objects.filter(member=self.member, check_in_date=self.today).exists())
        self.assertIsNotNone(Attendance.objects.get(member=self.member).check_out_at)

    def test_member_directory_check_in_action_has_no_end_time_input(self):
        response = self.client.get(reverse('member_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="check_out_at"')

    def test_reset_portal_password(self):
        self.client.post(reverse('member_add'), {
            'full_name': 'Portal Person', 'phone': '9111111111', 'plan': '', 'start_date': '',
            'payment_method': 'cash',
        })
        member = MemberProfile.objects.get(phone='9111111111')
        user = User.objects.get(username='9111111111')
        old_hash = user.password
        self.client.post(reverse('member_reset_password', args=[member.member_id]))
        user.refresh_from_db()
        self.assertNotEqual(user.password, old_hash)

    # --- lists and filters ---------------------------------------------------------
    def test_member_list_filters(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=3), 'active')
        MemberProfile.objects.create(gym=self.gym, full_name='No Plan', phone='9000000002', status='active')
        expiring = self.client.get(reverse('member_list'), {'membership': 'expiring'})
        self.assertContains(expiring, 'Ravi Kumar')
        self.assertNotContains(expiring, 'No Plan')
        none = self.client.get(reverse('member_list'), {'membership': 'none'})
        self.assertContains(none, 'No Plan')
        self.assertNotContains(none, 'Ravi Kumar')
        due = self.client.get(reverse('member_list'), {'membership': 'due'})
        self.assertContains(due, 'Ravi Kumar')

    def test_member_list_has_direct_call_and_whatsapp_links(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=3), 'active')
        response = self.client.get(reverse('member_list'))
        self.assertContains(response, 'tel:9000000001')
        self.assertContains(response, 'https://wa.me/9000000001')

    def test_subscription_list_filters_and_search(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=3), 'active')
        for params in ({}, {'filter': 'expiring'}, {'filter': 'due'}, {'search': 'Ravi'}, {'status': 'expired'}):
            self.assertEqual(self.client.get(reverse('subscription_list'), params).status_code, 200)
        self.assertContains(self.client.get(reverse('subscription_list'), {'filter': 'due'}), 'Ravi Kumar')

    def test_expired_subscription_list_includes_call_and_whatsapp_actions(self):
        self._sub(self.today - timedelta(days=40), self.today - timedelta(days=10), 'expired')

        response = self.client.get(reverse('subscription_list'), {'status': 'expired'})

        self.assertContains(response, 'href="tel:9000000001"')
        self.assertContains(response, 'href="https://wa.me/9000000001"')
        self.assertContains(response, 'title="Call member"')
        self.assertContains(response, 'title="WhatsApp member"')

    def test_key_admin_pages_render(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=3), 'active')
        sub = MemberSubscription.objects.get(member=self.member)
        urls = [
            reverse('dashboard'), reverse('member_list'), reverse('member_add'),
            reverse('member_detail', args=[self.member.member_id]),
            reverse('member_edit', args=[self.member.member_id]),
            reverse('subscription_list'), reverse('subscription_add'),
            reverse('subscription_add') + f'?member={self.member.pk}',
            reverse('subscription_renew', args=[sub.id]),
            reverse('plan_list'), reverse('plan_add'), reverse('plan_edit', args=[self.plan.id]),
            reverse('payment_add') + f'?member={self.member.member_id}&subscription_id={sub.id}',
            reverse('outstanding_payments'),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_dashboard_omits_renewals_and_fees_panels(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=2), 'active')
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Renewals due this week')
        self.assertNotContains(response, 'Fees to collect')

    def test_dashboard_does_not_duplicate_at_risk_notifications(self):
        for index in range(5):
            member = self.member if index == 0 else MemberProfile.objects.create(
                gym=self.gym,
                full_name=f'At-risk member {index}',
                phone=f'900000001{index}',
                status='active',
            )
            member.last_attendance_date = timezone.now() - timedelta(days=3)
            member.save(update_fields=['last_attendance_date'])

        self.client.get(reverse('dashboard'))
        self.client.get(reverse('retention_list'))
        notifications_after_first_visit = Notification.objects.filter(
            gym=self.gym,
            recipient=self.owner,
            notification_type='inactivity_alert',
        ).count()
        self.client.get(reverse('dashboard'))
        self.client.get(reverse('retention_list'))
        notifications_after_second_visit = Notification.objects.filter(
            gym=self.gym,
            recipient=self.owner,
            notification_type='inactivity_alert',
        ).count()

        self.assertEqual(notifications_after_first_visit, 5)
        self.assertEqual(notifications_after_second_visit, 5)

    def test_dashboard_counts_distinct_members_with_current_memberships(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=2), 'active')
        self._sub(self.today - timedelta(days=10), self.today + timedelta(days=10), 'active')
        MemberProfile.objects.create(
            gym=self.gym,
            full_name='Profile without membership',
            phone='9000000003',
            status='active',
        )

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['active_members'], 1)
        self.assertContains(response, '?membership=active')
        self.assertContains(response, 'Members with a valid membership')

    def test_dashboard_counts_expired_memberships(self):
        self._sub(self.today - timedelta(days=60), self.today - timedelta(days=30), 'expired')
        self._sub(self.today - timedelta(days=20), self.today - timedelta(days=1), 'active')
        self._sub(self.today - timedelta(days=2), self.today + timedelta(days=28), 'active')

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['expired_subscriptions'], 2)
        self.assertContains(response, 'Memberships expired')
        self.assertContains(response, '?status=expired')

    def test_dashboard_shows_live_occupancy_for_today_check_ins(self):
        self._sub(self.today - timedelta(days=20), self.today + timedelta(days=3), 'active')
        Attendance.objects.create(
            gym=self.gym,
            member=self.member,
            check_in_date=self.today,
            check_in_at=timezone.now() - timedelta(minutes=30),
            source='qr',
        )

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Live occupancy')
        self.assertContains(response, '1')

    def test_dashboard_excludes_visits_that_have_reached_their_end_time(self):
        now = timezone.now()
        Attendance.objects.create(
            gym=self.gym,
            member=self.member,
            check_in_date=self.today,
            check_in_at=now - timedelta(hours=2),
            check_out_at=now - timedelta(minutes=1),
            source='qr',
        )
        current_member = MemberProfile.objects.create(
            gym=self.gym,
            full_name='Current visitor',
            phone='9000000004',
            status='active',
        )
        Attendance.objects.create(
            gym=self.gym,
            member=current_member,
            check_in_date=self.today,
            check_in_at=now - timedelta(minutes=30),
            check_out_at=now + timedelta(hours=1),
            source='qr',
        )

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['live_occupancy'], 1)

    def test_dashboard_live_occupancy_only_includes_check_ins_from_last_two_hours(self):
        now = timezone.now()
        recent_member = self.member
        older_member = MemberProfile.objects.create(
            gym=self.gym,
            full_name='Earlier visitor',
            phone='9000000005',
            status='active',
        )
        Attendance.objects.create(
            gym=self.gym,
            member=recent_member,
            check_in_date=self.today,
            check_in_at=now - timedelta(minutes=90),
            check_out_at=now + timedelta(hours=1),
            source='qr',
        )
        Attendance.objects.create(
            gym=self.gym,
            member=older_member,
            check_in_date=self.today,
            check_in_at=now - timedelta(hours=3),
            check_out_at=now + timedelta(hours=1),
            source='qr',
        )

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['live_occupancy'], 1)
        self.assertContains(response, 'last 2 hours')

    def test_dashboard_renders_attendance_chart_as_svg(self):
        Attendance.objects.create(
            gym=self.gym,
            member=self.member,
            check_in_date=self.today,
            check_in_at=timezone.now(),
            source='qr',
        )

        response = self.client.get(reverse('dashboard'))
        content = response.content.decode()
        self.assertContains(response, 'id="attendance-trend-chart"')
        self.assertEqual(len(re.findall(r'<circle\b', content)), 7)
        self.assertRegex(content, r'<title>[^<]+: 1 visits</title>')
