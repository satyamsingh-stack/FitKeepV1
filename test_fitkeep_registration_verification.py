"""
Tests for FitKeep admin (step 2) registration verification.
Covers new registration flow, OTP service, views/URLs, middleware, and templates.
"""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.middleware import RateLimitMiddleware
from gyms.models import Gym, GymWorkspaceOTP
from accounts.models import GymMembership
from gyms.workspace_otp import (
    OTPVerificationError,
    _active_otp_for,
    _get_fitkeep_admin_email,
    _send_workspace_otp_email,
    _verify_common,
    create_registration_otp,
    get_registration_fitkeep_admin_otp_status,
    get_registration_otp_status,
    resend_registration_fitkeep_admin_otp,
    verify_registration_fitkeep_admin_otp,
    verify_registration_otp,
)

User = get_user_model()
ADMIN_EMAIL = 'satyamakhilesh2000@gmail.com'


def _clear_rate_cache():
    try:
        cache.clear()
    except Exception:
        pass


def _request_with_session(rf, path='/', method='get'):
    req = getattr(rf, method)(path)
    from django.contrib.sessions.middleware import SessionMiddleware
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    return req


REG_DATA = {'name': 'Owner', 'email': 'newowner@example.com', 'phone': '9999999999', 'password': 'StrongPass123!', 'gym_name': 'New Gym', 'gym_address': 'Addr'}


class FitKeepAdminOTPServiceTests(TestCase):
    def setUp(self):
        _clear_rate_cache()
        self.rf = RequestFactory()

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_owner_verify_issues_fitkeep_admin_otp_and_does_not_create_account(self):
        req = _request_with_session(self.rf, '/register/')
        create_registration_otp(registration_data=REG_DATA, request=req)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            result = verify_registration_otp(code='123456', request=req)
        self.assertEqual(result, {'next': 'fitkeep_admin_verification', 'owner_email': 'newowner@example.com', 'admin_email': ADMIN_EMAIL})
        self.assertFalse(User.objects.filter(email='newowner@example.com').exists())
        self.assertFalse(Gym.objects.filter(name='New Gym').exists())
        self.assertIsNone(get_registration_otp_status(request=req))
        status = get_registration_fitkeep_admin_otp_status(request=req)
        self.assertIsNotNone(status)
        self.assertEqual(status['gym_name'], 'New Gym')
        otp = _active_otp_for(session_key=req.session.session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
        self.assertEqual(otp.email, ADMIN_EMAIL)
        payload = json.loads(otp.pending_data)
        self.assertTrue(payload.get('owner_email_verified'))
        self.assertTrue(any('FitKeep admin verification' in m.subject and ADMIN_EMAIL in m.to for m in mail.outbox))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_fitkeep_admin_verify_creates_workspace(self):
        req = _request_with_session(self.rf, '/register/')
        create_registration_otp(registration_data=REG_DATA, request=req)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            verify_registration_otp(code='123456', request=req)
        fitkeep = _active_otp_for(session_key=req.session.session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
        GymWorkspaceOTP.objects.filter(pk=fitkeep.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
        resend_registration_fitkeep_admin_otp(request=req)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            user, gym = verify_registration_fitkeep_admin_otp(code='123456', request=req)
        self.assertEqual(user.email, 'newowner@example.com')
        self.assertTrue(user.check_password('StrongPass123!'))
        self.assertEqual(gym.name, 'New Gym')
        self.assertTrue(GymMembership.objects.filter(user=user, gym=gym, role='owner', is_primary=True).exists())
        self.assertIsNone(get_registration_fitkeep_admin_otp_status(request=req))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_fitkeep_admin_verify_missing_and_expired(self):
        req = _request_with_session(self.rf, '/register/')
        with self.assertRaisesMessage(OTPVerificationError, 'expired'):
            verify_registration_fitkeep_admin_otp(code='123456', request=req)
        with self.assertRaisesMessage(OTPVerificationError, 'expired'):
            resend_registration_fitkeep_admin_otp(request=req)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_common_missing_invalid_and_pre_max_attempts_cover_new_logic(self):
        req = _request_with_session(self.rf)
        with self.assertRaisesMessage(OTPVerificationError, 'expired'):
            _verify_common(None, '123456', expired_message='expired')
        otp = GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data='{}', expires_at=timezone.now()+timedelta(minutes=5))
        with self.assertRaisesMessage(OTPVerificationError, '6-digit verification code'):
            _verify_common(otp, 'abc', expired_message='expired')
        otp.attempts = otp.max_attempts; otp.save(update_fields=['attempts'])
        with self.assertRaisesMessage(OTPVerificationError, 'Too many incorrect attempts'):
            _verify_common(otp, '123456', expired_message='expired')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_owner_step_rejects_duplicate_email_and_phone_before_fitkeep(self):
        req = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='dup@example.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, code_hash='h', pending_data=json.dumps({'name':'N','email':'dup@example.com','phone':'','password_hash':'h','gym_name':'G'}), expires_at=timezone.now()+timedelta(minutes=5))
        User.objects.create_user(username='dup@example.com', email='dup@example.com', password='StrongPass123!')
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'email is no longer available'):
                verify_registration_otp(code='123456', request=req)
        req2 = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req2.session.session_key, email='dup2@example.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, code_hash='h', pending_data=json.dumps({'name':'N','email':'dup2@example.com','phone':'7777777777','password_hash':'h','gym_name':'G'}), expires_at=timezone.now()+timedelta(minutes=5))
        User.objects.create_user(username='pdup@example.com', email='pdup@example.com', phone='7777777777', password='StrongPass123!')
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'phone number is no longer available'):
                verify_registration_otp(code='123456', request=req2)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_registration_otp_phone_race_after_common_verify(self):
        req = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='race2@example.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, code_hash='h', pending_data=json.dumps({'name':'N','email':'race2@example.com','phone':'7777777777','password_hash':'h','gym_name':'G'}), expires_at=timezone.now()+timedelta(minutes=5))
        real_filter = User.objects.filter
        def filter_side_effect(*a, **k):
            if k.get('email') == 'race2@example.com':
                class Q:
                    calls = 0
                    def exists(self): Q.calls += 1; return Q.calls > 1
                return Q()
            if k.get('phone') == '7777777777':
                class Q:
                    def exists(self): return True
                return Q()
            return real_filter(*a, **k)
        with patch('gyms.workspace_otp.check_password', return_value=True), patch.object(User.objects, 'filter', side_effect=filter_side_effect):
            with self.assertRaisesMessage(OTPVerificationError, 'phone number is no longer available'):
                verify_registration_otp(code='123456', request=req)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_fitkeep_admin_guards_no_account_created(self):
        req = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email=ADMIN_EMAIL, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data=json.dumps({'name':'N','email':'g@example.com','phone':'','password_hash':'h','gym_name':'G','owner_email_verified': False}), expires_at=timezone.now()+timedelta(minutes=5))
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'Owner email verification is required'):
                verify_registration_fitkeep_admin_otp(code='123456', request=req)
        self.assertFalse(User.objects.filter(email='g@example.com').exists())
        req2 = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req2.session.session_key, email=ADMIN_EMAIL, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data=json.dumps({'name':'N','email':'g2@example.com','phone':'6666666666','password_hash':'h','gym_name':'G','owner_email_verified': True}), expires_at=timezone.now()+timedelta(minutes=5))
        User.objects.create_user(username='pp@example.com', email='pp@example.com', phone='6666666666', password='StrongPass123!')
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'phone number is no longer available'):
                verify_registration_fitkeep_admin_otp(code='123456', request=req2)
        self.assertFalse(User.objects.filter(email='g2@example.com').exists())

    @override_settings(FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_fitkeep_admin_email_helper_and_subject(self):
        self.assertEqual(_get_fitkeep_admin_email(), ADMIN_EMAIL)
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            _send_workspace_otp_email(email=ADMIN_EMAIL, code='123456', gym_name='Gym', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
        self.assertIn('FitKeep admin verification', mail.outbox[-1].subject)


class FitKeepRegistrationViewsTests(TestCase):
    def setUp(self):
        _clear_rate_cache()
        self.client = Client()

    def _seed_owner_step(self):
        data = {**REG_DATA, 'password_confirm': REG_DATA['password']}
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL):
            resp = self.client.post(reverse('register_gym'), data)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('register_gym_verify'), resp['Location'])

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_full_two_step_registration_view_flow(self):
        self._seed_owner_step()
        with patch('gyms.workspace_otp.check_password', return_value=True):
            resp = self.client.post(reverse('register_gym_verify'), {'otp_code': '123456'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('register_gym_fitkeep_verify'), resp['Location'])
        resp = self.client.get(reverse('register_gym_fitkeep_verify'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Step 2 of 2')
        self.assertContains(resp, 'FitKeep verification')
        fitkeep = _active_otp_for(session_key=self.client.session.session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
        GymWorkspaceOTP.objects.filter(pk=fitkeep.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
        resp = self.client.post(reverse('register_gym_fitkeep_resend_otp'))
        self.assertEqual(resp.status_code, 302)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            resp = self.client.post(reverse('register_gym_fitkeep_verify'), {'otp_code': '123456'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(email='newowner@example.com').exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_fitkeep_verify_redirects_and_guards(self):
        resp = self.client.get(reverse('register_gym_fitkeep_verify'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('register_gym'), resp['Location'])
        self._seed_owner_step()
        resp = self.client.get(reverse('register_gym_fitkeep_verify'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('register_gym_verify'), resp['Location'])
        with patch('gyms.workspace_otp.check_password', return_value=True):
            self.client.post(reverse('register_gym_verify'), {'otp_code': '123456'})
        user = User.objects.create_user(username='u2@example.com', email='u2@example.com', password='StrongPass123!')
        c = Client(); c.force_login(user)
        resp = c.get(reverse('register_gym_fitkeep_verify'))
        self.assertEqual(resp.status_code, 302)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_fitkeep_verify_post_error_and_missing_status(self):
        self._seed_owner_step()
        with patch('gyms.workspace_otp.check_password', return_value=True):
            self.client.post(reverse('register_gym_verify'), {'otp_code': '123456'})
        with patch('gyms.views.verify_registration_fitkeep_admin_otp', side_effect=OTPVerificationError('bad')):
            resp = self.client.post(reverse('register_gym_fitkeep_verify'), {'otp_code': '123456'})
        self.assertEqual(resp.status_code, 200)
        GymWorkspaceOTP.objects.filter(purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP).update(consumed_at=timezone.now())
        with patch('gyms.views.verify_registration_fitkeep_admin_otp', side_effect=OTPVerificationError('bad')):
            resp = self.client.post(reverse('register_gym_fitkeep_verify'), {'otp_code': '123456'})
        self.assertEqual(resp.status_code, 302)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_fitkeep_resend_errors(self):
        resp = self.client.post(reverse('register_gym_fitkeep_resend_otp'))
        self.assertEqual(resp.status_code, 302)
        with patch('gyms.views.resend_registration_fitkeep_admin_otp', side_effect=OTPVerificationError('wait')):
            resp = self.client.post(reverse('register_gym_fitkeep_resend_otp'))
        self.assertEqual(resp.status_code, 302)
        with patch('gyms.views.resend_registration_fitkeep_admin_otp', side_effect=Exception('boom')):
            resp = self.client.post(reverse('register_gym_fitkeep_resend_otp'))
        self.assertEqual(resp.status_code, 302)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL=ADMIN_EMAIL)
    def test_register_verify_redirects_to_fitkeep_when_admin_pending(self):
        self._seed_owner_step()
        with patch('gyms.workspace_otp.check_password', return_value=True):
            self.client.post(reverse('register_gym_verify'), {'otp_code': '123456'})
        resp = self.client.get(reverse('register_gym_verify'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('register_gym_fitkeep_verify'), resp['Location'])


class FitKeepMiddlewareRateLimitTests(TestCase):
    def test_fitkeep_paths_rate_limited(self):
        _clear_rate_cache()
        factory = RequestFactory()
        mw = RateLimitMiddleware(lambda request: type('R', (), {'status_code': 200})())
        for path, limit in [('/register/fitkeep-verify/', 20), ('/register/fitkeep-resend-otp/', 10)]:
            _clear_rate_cache(); status = 200
            for _ in range(limit + 2):
                status = mw(factory.post(path, REMOTE_ADDR='1.2.3.4')).status_code
            self.assertEqual(status, 429, path)
