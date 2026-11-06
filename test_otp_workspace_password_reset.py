"""
Tests for gym workspace/registration OTP and OTP-based password reset.
Covers workspace_otp service functions, OTP forms, OTP model helpers,
password reset views/URLs, and auth rate-limit middleware paths.
"""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import GymWorkspaceCreateForm, GymWorkspaceOTPVerifyForm, PasswordResetForm
from accounts.middleware import RateLimitMiddleware
from accounts.models import GymMembership, GymSettings
from gyms.models import Gym, GymWorkspaceOTP
from gyms.workspace_otp import (
    OTPVerificationError,
    create_password_reset_otp,
    create_registration_otp,
    create_workspace_otp,
    get_password_reset_otp_status,
    get_registration_fitkeep_admin_otp_status,
    get_registration_otp_status,
    get_workspace_otp_status,
    resend_password_reset_otp,
    resend_registration_fitkeep_admin_otp,
    resend_registration_otp,
    resend_workspace_otp,
    verify_password_reset_otp,
    verify_registration_fitkeep_admin_otp,
    verify_registration_otp,
    verify_workspace_otp,
    _active_otp_for,
    _consume,
    _ensure_session_key,
    _finalize_otp,
    _generate_code,
    _issue_otp,
    _lock_active,
    _mask_email,
    _normalize_email,
    _resend_otp,
    _scope_q,
    _send_workspace_otp_email,
    _status_payload,
    _verify_common,
)
from audit.models import AuditLog

User = get_user_model()


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


class OTPHelperTests(TestCase):
    def test_generate_code_is_six_digits(self):
        for _ in range(30):
            code = _generate_code()
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())

    def test_normalize_and_mask_email(self):
        self.assertEqual(_normalize_email('  User@Example.COM '), 'user@example.com')
        self.assertEqual(_mask_email('ab@example.com'), 'a*@example.com')
        self.assertEqual(_mask_email('a@example.com'), 'a*@example.com')
        self.assertEqual(_mask_email('alex@example.com'), 'a**x@example.com')
        self.assertEqual(_mask_email('not-an-email'), 'not-an-email')

    def test_scope_q_variants_and_error(self):
        user = User.objects.create_user(username='u1@example.com', email='u1@example.com', password='StrongPass123!')
        self.assertEqual(_scope_q(user=user, purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE)['user'], user)
        self.assertEqual(_scope_q(session_key='sess', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM)['session_key'], 'sess')
        with self.assertRaises(OTPVerificationError):
            _scope_q(user=None, session_key=None, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM)

    def test_ensure_session_key_creates_session_and_none(self):
        rf = RequestFactory()
        req = _request_with_session(rf, '/password-reset/')
        self.assertTrue(_ensure_session_key(req))
        with self.assertRaises(OTPVerificationError):
            _ensure_session_key(None)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', GYM_WORKSPACE_OTP_FROM_EMAIL='satyams0478@gmail.com')
    def test_send_workspace_otp_email_subjects(self):
        _send_workspace_otp_email(email='t@example.com', code='123456', gym_name='Iron', purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE)
        _send_workspace_otp_email(email='t@example.com', code='123456', gym_name='Iron', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM)
        _send_workspace_otp_email(email='t@example.com', code='123456', gym_name='FitKeep', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn('Verify your FitKeep gym workspace', mail.outbox[0].subject)
        self.assertIn('account and gym workspace', mail.outbox[1].subject)
        self.assertIn('password reset code', mail.outbox[2].subject)
        self.assertTrue(mail.outbox[2].alternatives)

    def test_active_otp_consumes_expired_and_status_payload(self):
        req = _request_with_session(RequestFactory())
        key = req.session.session_key
        expired = GymWorkspaceOTP.objects.create(
            session_key=key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET,
            code_hash='hash', pending_data=json.dumps({'gym_name': 'FitKeep'}), expires_at=timezone.now() - timedelta(minutes=1), last_sent_at=timezone.now(),
        )
        self.assertIsNone(_active_otp_for(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET))
        expired.refresh_from_db(); self.assertIsNotNone(expired.consumed_at)
        self.assertIsNone(_status_payload(None))
        otp = GymWorkspaceOTP.objects.create(
            session_key=key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET,
            code_hash='hash', pending_data='not-json', expires_at=timezone.now() + timedelta(minutes=5), last_sent_at=timezone.now(),
        )
        payload = _status_payload(otp)
        self.assertEqual(payload['masked_email'], 'x*@example.com')
        self.assertEqual(payload['gym_name'], '')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_issue_otp_validation_cooldown_and_hourly_limit(self):
        req = _request_with_session(RequestFactory())
        key = req.session.session_key
        with self.assertRaises(OTPVerificationError):
            _issue_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, email='', payload={}, request=req)
        otp = _issue_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, email='x@example.com', payload={'gym_name': 'FitKeep'}, request=req)
        with self.assertRaises(OTPVerificationError):
            _issue_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, email='x@example.com', payload={'gym_name': 'FitKeep'}, request=req)
        GymWorkspaceOTP.objects.filter(pk=otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10), consumed_at=timezone.now())
        with patch('gyms.workspace_otp.OTP_MAX_ACTIVE_REQUESTS_PER_HOUR', 1):
            with self.assertRaises(OTPVerificationError):
                _issue_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, email='x@example.com', payload={'gym_name': 'FitKeep'}, request=req)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_registration_fitkeep_admin_requires_verified_owner_flag(self):
        req = _request_with_session(RequestFactory(), '/register/')
        GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='satyamakhilesh2000@gmail.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data=json.dumps({'name':'N','email':'g9@example.com','phone':'','password_hash':'h','gym_name':'G','owner_email_verified': False}), expires_at=timezone.now()+timedelta(minutes=5))
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'Owner email verification is required'):
                verify_registration_fitkeep_admin_otp(code='123456', request=req)
        req = _request_with_session(RequestFactory())
        with self.assertRaisesMessage(OTPVerificationError, 'expired'):
            _verify_common(None, '123456', expired_message='expired')
        otp = GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now()+timedelta(minutes=5), attempts=5, max_attempts=5)
        with self.assertRaisesMessage(OTPVerificationError, 'Too many incorrect attempts'):
            _verify_common(otp, '123456', expired_message='expired')
        otp.refresh_from_db(); self.assertIsNotNone(otp.consumed_at)
        otp2 = GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now()+timedelta(minutes=5))
        with self.assertRaisesMessage(OTPVerificationError, '6-digit verification code'):
            _verify_common(otp2, 'abc', expired_message='expired')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_common_wrong_password_paths(self):
        req = _request_with_session(RequestFactory())
        otp = GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data=json.dumps({'gym_name':'FitKeep'}), expires_at=timezone.now()+timedelta(minutes=5), attempts=0, max_attempts=5)
        with patch('gyms.workspace_otp.check_password', return_value=False):
            with self.assertRaisesMessage(OTPVerificationError, '4 attempts remaining'):
                _verify_common(otp, '000000', expired_message='expired')
        otp.refresh_from_db(); otp.attempts = 4; otp.save(update_fields=['attempts'])
        with patch('gyms.workspace_otp.check_password', return_value=False):
            with self.assertRaisesMessage(OTPVerificationError, 'Too many attempts were made'):
                _verify_common(otp, '000000', expired_message='expired')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_common_bad_json_after_check(self):
        req = _request_with_session(RequestFactory())
        key = req.session.session_key
        otp = _issue_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, email='x@example.com', payload={'gym_name': 'FitKeep'}, request=req)
        GymWorkspaceOTP.objects.filter(pk=otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10), pending_data='bad')
        otp.refresh_from_db()
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaises(OTPVerificationError):
                _verify_common(otp, '123456', expired_message='expired')
        otp.refresh_from_db()
        self.assertIsNotNone(otp.consumed_at)

    def test_status_helpers_return_none_on_session_error(self):
        class BadReq:
            @property
            def session(self):
                raise RuntimeError('no session')
        self.assertIsNone(get_registration_otp_status(request=BadReq()))
        self.assertIsNone(get_password_reset_otp_status(request=BadReq()))
        self.assertIsNone(get_registration_fitkeep_admin_otp_status(request=BadReq()))

    def test_lock_consume_finalize_and_resend_errors(self):
        req = _request_with_session(RequestFactory())
        key = req.session.session_key
        expired = GymWorkspaceOTP.objects.create(session_key=key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(OTPVerificationError):
            _lock_active(expired)
        active = GymWorkspaceOTP.objects.create(session_key=key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now() + timedelta(minutes=5))
        _consume(active); active.refresh_from_db(); _consume(active); self.assertIsNotNone(active.consumed_at)
        a = GymWorkspaceOTP.objects.create(session_key=key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now() + timedelta(minutes=5))
        b = GymWorkspaceOTP.objects.create(session_key=key, email='x@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now() + timedelta(minutes=5))
        _finalize_otp(a, session_key=key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
        b.refresh_from_db(); self.assertIsNotNone(b.consumed_at)
        with self.assertRaises(OTPVerificationError):
            _resend_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, start_over_message='start', request=req)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_resend_otp_cooldown_and_payload_fallback(self):
        req = _request_with_session(RequestFactory())
        key = req.session.session_key
        otp = _issue_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, email='r@example.com', payload={'gym_name': 'G'}, request=req)
        with self.assertRaises(OTPVerificationError):
            _resend_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, start_over_message='start', request=req)
        GymWorkspaceOTP.objects.filter(pk=otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10), pending_data='bad-json')
        resent = _resend_otp(session_key=key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM, start_over_message='start', request=req)
        self.assertEqual(resent.pk, otp.pk)


class WorkspaceAndRegistrationOTPServiceTests(TestCase):
    def setUp(self):
        _clear_rate_cache()
        self.rf = RequestFactory()
        self.user = User.objects.create_user(username='owner@example.com', email='owner@example.com', password='StrongPass123!', role='gym_owner')
        self.req = _request_with_session(self.rf)
        self.req.user = self.user

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_workspace_otp_create_resend_verify_and_status(self):
        with self.assertRaises(OTPVerificationError):
            create_workspace_otp(user=None, gym_data={'gym_name': 'G'}, request=self.req)
        noemail = User.objects.create_user(username='noemail', password='StrongPass123!')
        with self.assertRaises(OTPVerificationError):
            create_workspace_otp(user=noemail, gym_data={'gym_name': 'G'}, request=self.req)
        with self.assertRaises(OTPVerificationError):
            create_workspace_otp(user=self.user, gym_data={'gym_name': ''}, request=self.req)
        otp = create_workspace_otp(user=self.user, gym_data={'gym_name': 'Test Gym', 'gym_address': 'Addr'}, request=self.req)
        self.assertIsNotNone(get_workspace_otp_status(user=self.user))
        GymWorkspaceOTP.objects.filter(pk=otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
        resend_workspace_otp(user=self.user, request=self.req)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            gym = verify_workspace_otp(user=self.user, code='123456', request=self.req)
        self.assertEqual(gym.name, 'Test Gym')
        membership = GymMembership.objects.get(user=self.user, gym=gym)
        self.assertEqual(membership.role, 'owner')
        # GymMembership.save() normalizes owner memberships as primary for the gym.
        self.assertTrue(membership.is_primary)
        self.assertTrue(GymSettings.objects.filter(gym=gym).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FITKEEP_ADMIN_VERIFICATION_EMAIL='satyamakhilesh2000@gmail.com')
    def test_registration_otp_two_step_fitkeep_admin_verification(self):
        req = _request_with_session(self.rf, '/register/')
        data = {'name': 'Owner', 'email': 'newowner@example.com', 'phone': '9999999999', 'password': 'StrongPass123!', 'gym_name': 'New Gym', 'gym_address': 'Addr'}
        otp = create_registration_otp(registration_data=data, request=req)
        self.assertIsNotNone(get_registration_otp_status(request=req))
        GymWorkspaceOTP.objects.filter(pk=otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
        resend_registration_otp(request=req)
        req2 = _request_with_session(self.rf, '/register/')
        User.objects.create_user(username='taken@example.com', email='taken@example.com', password='StrongPass123!')
        with self.assertRaises(OTPVerificationError):
            create_registration_otp(registration_data={**data, 'email': 'taken@example.com'}, request=req2)
        # step 1: owner email verification moves to FitKeep admin verification
        with patch('gyms.workspace_otp.check_password', return_value=True):
            result = verify_registration_otp(code='123456', request=req)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get('next'), 'fitkeep_admin_verification')
        self.assertEqual(result.get('admin_email'), 'satyamakhilesh2000@gmail.com')
        self.assertIsNone(get_registration_otp_status(request=req))
        admin_status = get_registration_fitkeep_admin_otp_status(request=req)
        self.assertIsNotNone(admin_status)
        self.assertEqual(admin_status['masked_email'], _mask_email('satyamakhilesh2000@gmail.com'))
        self.assertFalse(User.objects.filter(email='newowner@example.com').exists())
        fitkeep_otp = _active_otp_for(session_key=req.session.session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
        self.assertIsNotNone(fitkeep_otp)
        GymWorkspaceOTP.objects.filter(pk=fitkeep_otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
        resend_registration_fitkeep_admin_otp(request=req)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            user, gym = verify_registration_fitkeep_admin_otp(code='123456', request=req)
        self.assertEqual(user.email, 'newowner@example.com')
        self.assertTrue(GymMembership.objects.filter(user=user, gym=gym, is_primary=True).exists())
        self.assertIsNone(get_registration_fitkeep_admin_otp_status(request=req))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_workspace_otp_guard_branches(self):
        with self.assertRaises(OTPVerificationError):
            create_workspace_otp(user=self.user, gym_data={}, request=self.req)
        # verify workspace with empty gym name payload
        otp = GymWorkspaceOTP.objects.create(user=self.user, email=self.user.email, purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE, code_hash='h', pending_data=json.dumps({'gym_name': ''}), expires_at=timezone.now()+timedelta(minutes=5))
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaises(OTPVerificationError):
                verify_workspace_otp(user=self.user, code='123456', request=self.req)
        # registration create guards
        req = _request_with_session(self.rf, '/register/')
        base = {'name':'N','email':'g@example.com','phone':'8888888888','password':'StrongPass123!','gym_name':'G'}
        with self.assertRaises(OTPVerificationError):
            create_registration_otp(registration_data={**base, 'gym_name': ''}, request=req)
        User.objects.create_user(username='phone@example.com', email='phone@example.com', phone='8888888888', password='StrongPass123!')
        req2 = _request_with_session(self.rf, '/register/')
        with self.assertRaises(OTPVerificationError):
            create_registration_otp(registration_data=base, request=req2)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_registration_fitkeep_admin_otp_expired_and_missing(self):
        req = _request_with_session(self.rf, '/register/')
        with self.assertRaisesMessage(OTPVerificationError, 'expired'):
            verify_registration_fitkeep_admin_otp(code='123456', request=req)
        # resend missing session guard
        with self.assertRaisesMessage(OTPVerificationError, 'expired'):
            resend_registration_fitkeep_admin_otp(request=req)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_registration_fitkeep_admin_verify_guards_and_email_subject(self):
        req = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='satyamakhilesh2000@gmail.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data=json.dumps({'name':'N','email':'g1@example.com','phone':'','password_hash':'h','gym_name':'G','owner_email_verified': False}), expires_at=timezone.now()+timedelta(minutes=5))
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'Owner email verification is required'):
                verify_registration_fitkeep_admin_otp(code='123456', request=req)
        req2 = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req2.session.session_key, email='satyamakhilesh2000@gmail.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data=json.dumps({'name':'N','email':'g2@example.com','phone':'','password_hash':'h','gym_name':'G','owner_email_verified': True}), expires_at=timezone.now()+timedelta(minutes=5))
        User.objects.create_user(username='g2@example.com', email='g2@example.com', password='StrongPass123!')
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'email is no longer available'):
                verify_registration_fitkeep_admin_otp(code='123456', request=req2)
        req3 = _request_with_session(self.rf, '/register/')
        GymWorkspaceOTP.objects.create(session_key=req3.session.session_key, email='satyamakhilesh2000@gmail.com', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP, code_hash='h', pending_data=json.dumps({'name':'N','email':'g3@example.com','phone':'6666666666','password_hash':'h','gym_name':'G','owner_email_verified': True}), expires_at=timezone.now()+timedelta(minutes=5))
        User.objects.create_user(username='p3@example.com', email='p3@example.com', phone='6666666666', password='StrongPass123!')
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaisesMessage(OTPVerificationError, 'phone number is no longer available'):
                verify_registration_fitkeep_admin_otp(code='123456', request=req3)
        _send_workspace_otp_email(email='satyamakhilesh2000@gmail.com', code='123456', gym_name='G', purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
        self.assertTrue(any('FitKeep admin verification' in m.subject for m in mail.outbox))


class PasswordResetOTPServiceTests(TestCase):
    def setUp(self):
        _clear_rate_cache()
        self.rf = RequestFactory()
        self.user = User.objects.create_user(username='reset@example.com', email='reset@example.com', password='OldStrong123!')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_create_resend_verify_status_password_reset_otp(self):
        req = _request_with_session(self.rf)
        self.assertFalse(create_password_reset_otp(email='missing@example.com', request=req))
        self.assertIsNone(get_password_reset_otp_status(request=req))
        self.assertTrue(create_password_reset_otp(email='reset@example.com', request=req))
        status = get_password_reset_otp_status(request=req)
        self.assertEqual(status['purpose'], GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
        otp = _active_otp_for(session_key=req.session.session_key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
        GymWorkspaceOTP.objects.filter(pk=otp.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
        resend_password_reset_otp(request=req)
        with patch('gyms.workspace_otp.check_password', return_value=True):
            user = verify_password_reset_otp(code='123456', request=req)
        self.assertEqual(user.pk, self.user.pk)
        self.assertIsNone(get_password_reset_otp_status(request=req))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verify_password_reset_invalid_user_payload(self):
        req = _request_with_session(self.rf, '/password-reset/verify/')
        GymWorkspaceOTP.objects.create(session_key=req.session.session_key, email='gone@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data=json.dumps({'user_id': 999999, 'email': 'gone@example.com'}), expires_at=timezone.now()+timedelta(minutes=5))
        with patch('gyms.workspace_otp.check_password', return_value=True):
            with self.assertRaises(OTPVerificationError):
                verify_password_reset_otp(code='123456', request=req)


class OTPFormsAndModelTests(TestCase):
    def test_workspace_create_form_and_verify_form(self):
        user = User.objects.create_user(username='f@example.com', email='f@example.com', password='StrongPass123!')
        form = GymWorkspaceCreateForm(data={'gym_name': 'G', 'gym_address': '', 'gym_phone': '', 'gym_email': '', 'timezone': 'Asia/Kolkata', 'currency': 'INR'}, user=user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(GymWorkspaceOTPVerifyForm(data={'otp_code': '12a456'}).is_valid())
        ok = GymWorkspaceOTPVerifyForm(data={'otp_code': '123456'})
        self.assertTrue(ok.is_valid(), ok.errors)
        self.assertEqual(ok.cleaned_data['otp_code'], '123456')
        # normalization runs in clean_otp_code when field-level length permits
        norm = GymWorkspaceOTPVerifyForm()
        norm.cleaned_data = {'otp_code': ' 123-456 '}
        self.assertEqual(norm.clean_otp_code(), '123456')

    def test_password_reset_form_and_otp_model_properties(self):
        User.objects.create_user(username='exists@example.com', email='exists@example.com', password='StrongPass123!')
        form = PasswordResetForm(data={'email': 'exists@example.com'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(form.user)
        otp = GymWorkspaceOTP.objects.create(email='m@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data='{}', expires_at=timezone.now()+timedelta(minutes=5))
        self.assertTrue(otp.is_active)
        self.assertIn('Workspace OTP', str(otp))
        otp.expires_at = timezone.now() - timedelta(seconds=1); otp.save(update_fields=['expires_at'])
        self.assertTrue(otp.is_expired)


class RateLimitMiddlewareTests(TestCase):
    def setUp(self):
        _clear_rate_cache()
        self.factory = RequestFactory()
        self.middleware = RateLimitMiddleware(lambda request: type('R', (), {'status_code': 200})())

    def test_limits_password_reset_paths_and_x_forwarded_for(self):
        for path, limit in [('/password-reset/verify/', 20), ('/password-reset/resend-otp/', 10), ('/password-reset/confirm/', 15), ('/password-reset/', 10)]:
            _clear_rate_cache()
            status = 200
            for _ in range(limit + 2):
                req = self.factory.post(path, HTTP_X_FORWARDED_FOR='1.2.3.4, 5.6.7.8', REMOTE_ADDR='9.9.9.9')
                resp = self.middleware(req)
                status = resp.status_code
            self.assertEqual(status, 429, path)


class PasswordResetViewsTests(TestCase):
    def setUp(self):
        _clear_rate_cache()
        self.user = User.objects.create_user(username='flow@example.com', email='flow@example.com', password='OldStrong123!')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_request_verify_confirm_success(self):
        resp = self.client.post(reverse('password_reset'), {'email': 'flow@example.com'}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('password_reset_verify'), resp['Location'])
        self.assertTrue(GymWorkspaceOTP.objects.filter(purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, consumed_at__isnull=True).exists())
        with patch('accounts.views.verify_password_reset_otp', return_value=self.user):
            resp = self.client.post(reverse('password_reset_verify'), {'otp_code': '123456'}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('password_reset_confirm'), resp['Location'])
        resp = self.client.post(reverse('password_reset_confirm'), {'new_password': 'NewStrong123!@#', 'new_password_confirm': 'NewStrong123!@#'}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password('NewStrong123!@#'))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_unknown_email_generic_and_legacy_redirect(self):
        resp = self.client.post(reverse('password_reset'), {'email': 'nobody@example.com'}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('login'), resp['Location'])
        resp = self.client.get('/password-reset/abc/token/', follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].endswith('/password-reset/'))

    def test_password_reset_verify_guards_and_resend(self):
        resp = self.client.get(reverse('password_reset_verify'), follow=False)
        self.assertEqual(resp.status_code, 302)
        session = self.client.session; session.save()
        GymWorkspaceOTP.objects.create(session_key=self.client.session.session_key, email='flow@example.com', purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET, code_hash='h', pending_data=json.dumps({'user_id': self.user.pk, 'email': 'flow@example.com'}), expires_at=timezone.now()+timedelta(minutes=5), last_sent_at=timezone.now()-timedelta(minutes=10))
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            resp = self.client.post(reverse('password_reset_resend_otp'), follow=False)
        self.assertEqual(resp.status_code, 302)
        with patch('accounts.views.verify_password_reset_otp', side_effect=OTPVerificationError('bad code')):
            resp = self.client.post(reverse('password_reset_verify'), {'otp_code': '123456'}, follow=False)
        self.assertEqual(resp.status_code, 200)

    def test_password_reset_confirm_invalid_session_and_errors(self):
        resp = self.client.get(reverse('password_reset_confirm'), follow=False)
        self.assertEqual(resp.status_code, 302)
        session = self.client.session
        session['password_reset_verified_user_id'] = self.user.pk
        session['password_reset_verified_at'] = str(int(__import__('time').time()))
        session.save()
        resp = self.client.post(reverse('password_reset_confirm'), {'new_password': 'NewStrong123!@#', 'new_password_confirm': 'Mismatch123!@#'})
        self.assertEqual(resp.status_code, 200)
        with patch('accounts.views.validate_password', side_effect=Exception('weak')):
            resp = self.client.post(reverse('password_reset_confirm'), {'new_password': 'weak', 'new_password_confirm': 'weak'})
        self.assertEqual(resp.status_code, 200)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_password_reset_request_otp_error_and_send_exception_paths(self):
        with patch('accounts.views.create_password_reset_otp', side_effect=OTPVerificationError('slow down')):
            resp = self.client.post(reverse('password_reset'), {'email': 'flow@example.com'})
        self.assertEqual(resp.status_code, 200)
        with patch('accounts.views.create_password_reset_otp', side_effect=Exception('smtp')):
            resp = self.client.post(reverse('password_reset'), {'email': 'flow@example.com'}, follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('login'), resp['Location'])
