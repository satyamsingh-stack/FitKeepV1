"""
OTP verification for gym workspace creation and gym registration (FitKeep).

Security model:
- OTPs are generated with the secrets module and stored only as a salted hash.
- Pending details (gym details / owner+gym registration payload) are stored server-side
  until verification succeeds; passwords are stored as Django password hashes.
- Verification and resend are rate-limited; OTPs expire and attempts are capped.
- No plaintext OTPs are stored; no sensitive email credentials are exposed to frontend.
"""
from __future__ import annotations

import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import GymMembership, GymSettings
from audit.models import AuditLog
from gyms.models import Gym, GymWorkspaceOTP

OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = int(getattr(settings, "GYM_WORKSPACE_OTP_EXPIRY_MINUTES", 15) or 15)
OTP_RESEND_COOLDOWN_SECONDS = 45
OTP_MAX_ATTEMPTS = 5
OTP_MAX_ACTIVE_REQUESTS_PER_HOUR = 5


class OTPVerificationError(Exception):
    pass


def _generate_code() -> str:
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _scope_q(*, user=None, session_key=None, purpose: str):
    if user and getattr(user, "is_authenticated", False):
        return {"user": user, "purpose": purpose}
    if session_key:
        return {"session_key": session_key, "purpose": purpose, "user__isnull": True}
    raise OTPVerificationError("Your verification session could not be found. Please start again.")


def _active_otp_for(*, user=None, session_key=None, purpose: str):
    now = timezone.now()
    scope = _scope_q(user=user, session_key=session_key, purpose=purpose)
    GymWorkspaceOTP.objects.filter(
        consumed_at__isnull=True, expires_at__lte=now, **scope
    ).update(consumed_at=now)
    return (
        GymWorkspaceOTP.objects.filter(consumed_at__isnull=True, expires_at__gt=now, **scope)
        .order_by("-created_at")
        .first()
    )


def _mask_email(email: str) -> str:
    email = _normalize_email(email)
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = (local[:1] + "*" * max(len(local) - 1, 1)) or "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def _ensure_session_key(request):
    if not request:
        raise OTPVerificationError("Your session could not be started. Please try again.")
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


FITKEEP_ADMIN_VERIFICATION_EMAIL = "satyamakhilesh2000@gmail.com"


def _get_fitkeep_admin_email() -> str:
    return _normalize_email(
        getattr(settings, "FITKEEP_ADMIN_VERIFICATION_EMAIL", None) or FITKEEP_ADMIN_VERIFICATION_EMAIL
    )


def _send_workspace_otp_email(*, email: str, code: str, gym_name: str, purpose: str) -> None:
    subject = "Verify your FitKeep gym workspace"
    if purpose == GymWorkspaceOTP.PURPOSE_REGISTER_GYM:
        subject = "Verify your FitKeep account and gym workspace"
    elif purpose == GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP:
        subject = "FitKeep admin verification required for new gym registration"
    elif purpose == GymWorkspaceOTP.PURPOSE_PASSWORD_RESET:
        subject = "Your FitKeep password reset code"
    from_email = getattr(settings, "GYM_WORKSPACE_OTP_FROM_EMAIL", None) or "satyams0478@gmail.com"
    context = {
        "code": code,
        "gym_name": gym_name or "FitKeep",
        "expiry_minutes": OTP_EXPIRY_MINUTES,
        "purpose": purpose,
        "otp_from_email": from_email,
    }
    text_body = render_to_string("emails/gym_workspace_otp.txt", context)
    html_body = render_to_string("emails/gym_workspace_otp.html", context)
    message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=from_email, to=[email])
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


def _issue_otp(*, user=None, session_key=None, purpose: str, email: str, payload: dict, request=None):
    email = _normalize_email(email)
    if not email:
        raise OTPVerificationError("A valid email address is required for verification.")
    now = timezone.now()
    scope = _scope_q(user=user, session_key=session_key, purpose=purpose)
    recent_count = GymWorkspaceOTP.objects.filter(created_at__gte=now - timedelta(hours=1), **scope).count()
    if recent_count >= OTP_MAX_ACTIVE_REQUESTS_PER_HOUR:
        raise OTPVerificationError("Too many verification requests. Please try again later.")
    existing = _active_otp_for(user=user, session_key=session_key, purpose=purpose)
    if existing and existing.last_sent_at:
        elapsed = (now - existing.last_sent_at).total_seconds()
        if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
            wait = int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)
            raise OTPVerificationError(f"Please wait {wait} seconds before requesting another code.")

    code = _generate_code()
    gym_name = (payload.get("gym_name") or "your gym").strip() or "your gym"
    with transaction.atomic():
        GymWorkspaceOTP.objects.filter(consumed_at__isnull=True, **scope).update(consumed_at=now)
        otp = GymWorkspaceOTP.objects.create(
            user=user if (user and getattr(user, "is_authenticated", False)) else None,
            session_key=session_key or "",
            email=email,
            purpose=purpose,
            code_hash=make_password(code),
            pending_data=json.dumps(payload),
            expires_at=now + timedelta(minutes=OTP_EXPIRY_MINUTES),
            last_sent_at=now,
            max_attempts=OTP_MAX_ATTEMPTS,
            request_ip=(request.META.get("REMOTE_ADDR") if request else None) or "",
        )
    _send_workspace_otp_email(email=email, code=code, gym_name=gym_name, purpose=purpose)
    return otp


def create_workspace_otp(*, user, gym_data: dict, request=None) -> GymWorkspaceOTP:
    if not user or not getattr(user, "is_authenticated", False):
        raise OTPVerificationError("You must be logged in to create a gym workspace.")
    email = _normalize_email(getattr(user, "email", ""))
    if not email:
        raise OTPVerificationError("Add a verified email address to your account before creating a gym workspace.")
    payload = {
        "gym_name": (gym_data.get("gym_name") or "").strip(),
        "gym_address": (gym_data.get("gym_address") or "").strip(),
        "gym_phone": (gym_data.get("gym_phone") or "").strip(),
        "gym_email": _normalize_email(gym_data.get("gym_email") or email),
        "timezone": (gym_data.get("timezone") or "Asia/Kolkata").strip() or "Asia/Kolkata",
        "currency": (gym_data.get("currency") or "INR").strip() or "INR",
    }
    if not payload["gym_name"]:
        raise OTPVerificationError("Gym name is required.")
    otp = _issue_otp(
        user=user,
        purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE,
        email=email,
        payload=payload,
        request=request,
    )
    AuditLog.log_action(
        actor=user,
        action="update",
        metadata={"event": "workspace_otp_sent", "email": _mask_email(email)},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
    )
    return otp


def create_registration_otp(*, registration_data: dict, request=None) -> GymWorkspaceOTP:
    session_key = _ensure_session_key(request)
    email = _normalize_email(registration_data.get("email"))
    User = get_user_model()
    if User.objects.filter(email=email).exists():
        raise OTPVerificationError("An account with this email address already exists.")
    phone = (registration_data.get("phone") or "").strip()
    if phone and User.objects.filter(phone=phone).exists():
        raise OTPVerificationError("A user with this phone number already exists.")
    gym_name = (registration_data.get("gym_name") or "").strip()
    if not gym_name:
        raise OTPVerificationError("Gym name is required.")
    payload = {
        "name": (registration_data.get("name") or "").strip(),
        "email": email,
        "phone": phone,
        "password_hash": make_password(registration_data.get("password") or ""),
        "gym_name": gym_name,
        "gym_address": (registration_data.get("gym_address") or "").strip(),
    }
    otp = _issue_otp(
        session_key=session_key,
        purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM,
        email=email,
        payload=payload,
        request=request,
    )
    AuditLog.log_action(
        actor=None,
        action="update",
        metadata={"event": "register_gym_otp_sent", "email": _mask_email(email)},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
    )
    return otp


def resend_workspace_otp(*, user, request=None) -> GymWorkspaceOTP:
    return _resend_otp(
        user=user,
        purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE,
        start_over_message="Your verification session expired. Please enter your gym details again.",
        request=request,
    )


def resend_registration_otp(*, request=None) -> GymWorkspaceOTP:
    session_key = _ensure_session_key(request)
    return _resend_otp(
        session_key=session_key,
        purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM,
        start_over_message="Your verification session expired. Please enter your registration details again.",
        request=request,
    )


def _resend_otp(*, user=None, session_key=None, purpose: str, start_over_message: str, request=None):
    existing = _active_otp_for(user=user, session_key=session_key, purpose=purpose)
    if not existing:
        raise OTPVerificationError(start_over_message)
    now = timezone.now()
    if existing.last_sent_at:
        elapsed = (now - existing.last_sent_at).total_seconds()
        if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
            wait = int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)
            raise OTPVerificationError(f"Please wait {wait} seconds before requesting another code.")
    try:
        payload = json.loads(existing.pending_data or "{}")
    except Exception:
        payload = {}
    gym_name = (payload.get("gym_name") or "your gym").strip() or "your gym"
    code = _generate_code()
    existing.code_hash = make_password(code)
    existing.attempts = 0
    existing.expires_at = now + timedelta(minutes=OTP_EXPIRY_MINUTES)
    existing.last_sent_at = now
    existing.consumed_at = None
    existing.save(update_fields=["code_hash", "attempts", "expires_at", "last_sent_at", "consumed_at", "updated_at"])
    _send_workspace_otp_email(email=existing.email, code=code, gym_name=gym_name, purpose=purpose)
    AuditLog.log_action(
        actor=user if (user and getattr(user, "is_authenticated", False)) else None,
        action="update",
        metadata={"event": f"{purpose}_otp_resent", "email": _mask_email(existing.email)},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
    )
    return existing


def verify_workspace_otp(*, user, code: str, request=None) -> Gym:
    otp = _active_otp_for(user=user, purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE)
    payload = _verify_common(
        otp,
        code,
        expired_message="Your verification code has expired. Please enter your gym details again.",
    )
    gym_name = (payload.get("gym_name") or "").strip()
    if not gym_name:
        _consume(otp)
        raise OTPVerificationError("Your gym details could not be found. Please start again.")
    with transaction.atomic():
        locked = _lock_active(otp)
        gym = Gym.objects.create(
            name=gym_name,
            address=(payload.get("gym_address") or "").strip(),
            phone=(payload.get("gym_phone") or "").strip(),
            email=(payload.get("gym_email") or user.email or "").strip(),
            timezone=(payload.get("timezone") or "Asia/Kolkata").strip() or "Asia/Kolkata",
            currency=(payload.get("currency") or "INR").strip() or "INR",
            status="active",
        )
        GymMembership.objects.create(user=user, gym=gym, role="owner", is_primary=False, status="active")
        GymSettings.objects.create(gym=gym)
        _finalize_otp(locked, user=user, purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE)
    AuditLog.log_action(
        actor=user, action="create", gym=gym, target=gym,
        metadata={"source": "workspace_otp_verified"},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
    )
    return gym


def verify_registration_otp(*, code: str, request=None):
    """Verify owner email, then require FitKeep admin verification before creation."""
    session_key = _ensure_session_key(request)
    otp = _active_otp_for(session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM)
    payload = _verify_common(
        otp,
        code,
        expired_message="Your verification code has expired. Please enter your registration details again.",
    )
    User = get_user_model()
    email = _normalize_email(payload.get("email"))
    phone = (payload.get("phone") or "").strip()
    if not email or User.objects.filter(email=email).exists():
        _consume(otp)
        raise OTPVerificationError("That email is no longer available. Please start registration again.")
    if phone and User.objects.filter(phone=phone).exists():
        _consume(otp)
        raise OTPVerificationError("That phone number is no longer available. Please start registration again.")
    admin_email = _get_fitkeep_admin_email()
    pending = dict(payload)
    pending["owner_email_verified"] = True
    pending["fitkeep_admin_email"] = admin_email
    with transaction.atomic():
        locked = _lock_active(otp)
        _finalize_otp(locked, session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM)
    _issue_otp(
        session_key=session_key,
        purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP,
        email=admin_email,
        payload=pending,
        request=request,
    )
    AuditLog.log_action(
        actor=None,
        action="update",
        metadata={"event": "register_gym_owner_verified_admin_otp_sent", "owner_email": _mask_email(email), "admin_email": _mask_email(admin_email)},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
    )
    return {"next": "fitkeep_admin_verification", "owner_email": email, "admin_email": admin_email}


def verify_registration_fitkeep_admin_otp(*, code: str, request=None):
    session_key = _ensure_session_key(request)
    otp = _active_otp_for(session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
    payload = _verify_common(
        otp,
        code,
        expired_message="FitKeep admin verification code has expired. Please restart registration.",
    )
    User = get_user_model()
    email = _normalize_email(payload.get("email"))
    phone = (payload.get("phone") or "").strip()
    if not email or User.objects.filter(email=email).exists():
        _consume(otp)
        raise OTPVerificationError("That email is no longer available. Please start registration again.")
    if phone and User.objects.filter(phone=phone).exists():
        _consume(otp)
        raise OTPVerificationError("That phone number is no longer available. Please start registration again.")
    if not payload.get("owner_email_verified"):
        _consume(otp)
        raise OTPVerificationError("Owner email verification is required. Please start registration again.")
    with transaction.atomic():
        locked = _lock_active(otp)
        user = User(
            username=email,
            email=email,
            phone=phone,
            role="gym_owner",
            first_name=(payload.get("name") or "").strip()[:150],
        )
        user.password = payload.get("password_hash") or make_password(secrets.token_urlsafe(24))
        user.save()
        gym = Gym.objects.create(
            name=(payload.get("gym_name") or "").strip(),
            address=(payload.get("gym_address") or "").strip(),
            phone=phone,
            email=email,
            status="active",
        )
        GymMembership.objects.create(user=user, gym=gym, role="owner", is_primary=True, status="active")
        GymSettings.objects.create(gym=gym)
        _finalize_otp(locked, session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
    AuditLog.log_action(
        actor=user, action="create", gym=gym, target=gym,
        metadata={"registration": "gym_owner", "verified_by": "email_otp_fitkeep_admin"},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
    )
    return user, gym


def _verify_common(otp, code: str, *, expired_message: str):
    if not otp:
        raise OTPVerificationError(expired_message)
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == OTP_LENGTH):
        raise OTPVerificationError("Enter the 6-digit verification code from your email.")
    if otp.attempts >= otp.max_attempts:
        _consume(otp)
        raise OTPVerificationError("Too many incorrect attempts. Please start again and request a new code.")
    if not check_password(code, otp.code_hash):
        otp.attempts += 1
        updates = ["attempts", "updated_at"]
        if otp.attempts >= otp.max_attempts:
            otp.consumed_at = timezone.now()
            updates.append("consumed_at")
            message = "That code is incorrect. Too many attempts were made; please request a new code."
        else:
            remaining = otp.max_attempts - otp.attempts
            message = f"That code is incorrect. {remaining} attempt{'s' if remaining != 1 else ''} remaining."
        otp.save(update_fields=updates)
        raise OTPVerificationError(message)
    try:
        return json.loads(otp.pending_data or "{}")
    except Exception:
        _consume(otp)
        raise OTPVerificationError("Your details could not be found. Please start again.")


def _lock_active(otp):
    locked = GymWorkspaceOTP.objects.select_for_update().filter(pk=otp.pk, consumed_at__isnull=True).first()
    if not locked or locked.expires_at <= timezone.now():
        raise OTPVerificationError("Your verification code has expired. Please start again.")
    return locked


def _consume(otp):
    if otp and otp.consumed_at is None:
        otp.consumed_at = timezone.now()
        otp.save(update_fields=["consumed_at", "updated_at"])


def _finalize_otp(otp, *, user=None, session_key=None, purpose: str):
    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at", "updated_at"])
    scope = _scope_q(user=user, session_key=session_key, purpose=purpose)
    GymWorkspaceOTP.objects.filter(consumed_at__isnull=True, **scope).exclude(pk=otp.pk).update(consumed_at=timezone.now())


def get_workspace_otp_status(*, user):
    otp = _active_otp_for(user=user, purpose=GymWorkspaceOTP.PURPOSE_CREATE_WORKSPACE)
    return _status_payload(otp)


def get_registration_otp_status(*, request):
    try:
        session_key = _ensure_session_key(request)
    except Exception:
        return None
    otp = _active_otp_for(session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM)
    return _status_payload(otp)


def get_registration_fitkeep_admin_otp_status(*, request):
    try:
        session_key = _ensure_session_key(request)
    except Exception:
        return None
    otp = _active_otp_for(session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP)
    return _status_payload(otp)


def resend_registration_fitkeep_admin_otp(*, request=None) -> GymWorkspaceOTP:
    session_key = _ensure_session_key(request)
    return _resend_otp(
        session_key=session_key,
        purpose=GymWorkspaceOTP.PURPOSE_REGISTER_GYM_FITKEEP,
        start_over_message="FitKeep admin verification expired. Please start registration again.",
        request=request,
    )


def create_password_reset_otp(*, email: str, request=None) -> bool:
    """Issue a password-reset OTP when an active account exists.

    Returns True if an OTP was sent, False if no active account was found.
    Raises OTPVerificationError for rate/validation issues that should be shown.
    The caller must keep the response generic to avoid account enumeration.
    """
    session_key = _ensure_session_key(request)
    email = _normalize_email(email)
    User = get_user_model()
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if not user or not getattr(user, 'email', None):
        return False
    otp = _issue_otp(
        session_key=session_key,
        purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET,
        email=_normalize_email(user.email),
        payload={
            'user_id': user.pk,
            'email': _normalize_email(user.email),
            'gym_name': 'FitKeep',
        },
        request=request,
    )
    AuditLog.log_action(
        actor=user,
        action='update',
        metadata={'event': 'password_reset_otp_sent', 'email': _mask_email(user.email)},
        ip_address=request.META.get('REMOTE_ADDR') if request else None,
    )
    return True


def resend_password_reset_otp(*, request=None) -> GymWorkspaceOTP:
    session_key = _ensure_session_key(request)
    return _resend_otp(
        session_key=session_key,
        purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET,
        start_over_message='Your verification session expired. Please request a new password reset code.',
        request=request,
    )


def verify_password_reset_otp(*, code: str, request=None):
    session_key = _ensure_session_key(request)
    otp = _active_otp_for(session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
    payload = _verify_common(
        otp,
        code,
        expired_message='Your verification code has expired. Please request a new password reset code.',
    )
    User = get_user_model()
    user = None
    user_id = payload.get('user_id')
    if user_id:
        user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user:
        email = _normalize_email(payload.get('email'))
        user = User.objects.filter(email__iexact=email, is_active=True).first() if email else None
    if not user:
        _consume(otp)
        raise OTPVerificationError('This reset request is no longer valid. Please request a new code.')
    with transaction.atomic():
        locked = _lock_active(otp)
        _finalize_otp(locked, session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
    AuditLog.log_action(
        actor=user,
        action='update',
        metadata={'event': 'password_reset_otp_verified'},
        ip_address=request.META.get('REMOTE_ADDR') if request else None,
    )
    return user


def get_password_reset_otp_status(*, request):
    try:
        session_key = _ensure_session_key(request)
    except Exception:
        return None
    otp = _active_otp_for(session_key=session_key, purpose=GymWorkspaceOTP.PURPOSE_PASSWORD_RESET)
    return _status_payload(otp)


def _status_payload(otp):
    if not otp:
        return None
    now = timezone.now()
    resend_available_in = 0
    if otp.last_sent_at:
        resend_available_in = max(0, int(OTP_RESEND_COOLDOWN_SECONDS - (now - otp.last_sent_at).total_seconds()))
    expires_in = max(0, int((otp.expires_at - now).total_seconds()))
    try:
        payload = json.loads(otp.pending_data or "{}")
    except Exception:
        payload = {}
    return {
        "otp": otp,
        "purpose": otp.purpose,
        "masked_email": _mask_email(otp.email),
        "resend_available_in": resend_available_in,
        "expires_in": expires_in,
        "gym_name": payload.get("gym_name") or "",
    }
