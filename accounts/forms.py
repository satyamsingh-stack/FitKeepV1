"""
Forms for authentication and gym onboarding.
"""
import re

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.utils.translation import gettext_lazy as _

from accounts.models import GymMembership
from gyms.models import Gym

# ---------------------------------------------------------------------------
# Disposable / throwaway email domain blocklist
# ---------------------------------------------------------------------------
_DISPOSABLE_DOMAINS = {
    'mailinator.com', 'guerrillamail.com', 'guerrillamail.net',
    'guerrillamail.org', 'guerrillamail.de', 'guerrillamail.biz',
    'guerrillamail.info', 'trashmail.com', 'trashmail.me', 'trashmail.net',
    'trashmail.at', 'trashmail.io', 'trashmail.org', 'tempmail.com',
    'temp-mail.org', 'yopmail.com', 'yopmail.fr', 'cool.fr.nf',
    'jetable.fr.nf', 'nospam.ze.tc', 'nomail.xl.cx', 'mega.zik.dj',
    'speed.1s.fr', 'courriel.fr.nf', 'moncourrier.fr.nf', 'monemail.fr.nf',
    'monmail.fr.nf', 'sharklasers.com', 'guerrillamailblock.com',
    'grr.la', 'spam4.me', 'maildrop.cc', 'dispostable.com',
    'throwam.com', 'spamgourmet.com', 'spamgourmet.net',
    'getairmail.com', 'fakemail.net', 'spamfree24.org', 'mailnull.com',
    'spamgob.com', 'throwaway.email', 'discard.email',
    'crap.in', 'fakeinbox.com', 'spamex.com', 'mailexpire.com',
    'filzmail.com', '33mail.com', '20minutemail.com', '10minutemail.com',
    '10minutemail.net', '10minutemail.org', 'minutemailbox.com',
    'emailondeck.com', 'tempinbox.com', 'tempinbox.co.uk',
    'spamherelots.com', 'binkmail.com', 'bobmail.info', 'chammy.info',
    'devnullmail.com', 'letthemeatspam.com', 'smellfear.com',
    'spamhereplease.com', 'thankyou2010.com', 'thisisnotmyrealemail.com',
    'tradermail.info', 'wegwerfmail.de', 'wegwerfmail.net', 'wegwerfmail.org',
}

# ---------------------------------------------------------------------------
# Shared strict email validator
# ---------------------------------------------------------------------------
_django_email_validator = EmailValidator()


def _validate_email_strict(email: str) -> str:
    """
    Normalise and strictly validate an email address.

    Checks (in order):
    1. Django's built-in EmailValidator (format, length, etc.)
    2. Local-part must not be empty or consist only of dots.
    3. Domain must have at least one dot + TLD of ≥ 2 characters.
    4. Domain must not be in the disposable-email blocklist.
    5. No consecutive dots anywhere in the address.
    6. Local-part must not start or end with a dot.

    Returns the normalised (lowercase) email on success.
    Raises ``ValidationError`` on any failure.
    """
    if not email:
        raise ValidationError(_('Email address is required.'))

    email = email.strip().lower()

    # 1. Django built-in check
    try:
        _django_email_validator(email)
    except ValidationError:
        raise ValidationError(_('Enter a valid email address (e.g. name@example.com).'))

    # Split into local and domain parts
    try:
        local, domain = email.rsplit('@', 1)
    except ValueError:
        raise ValidationError(_('Enter a valid email address (e.g. name@example.com).'))

    # 2. Local-part sanity
    if not local or local.strip('.') == '':
        raise ValidationError(_('The part before "@" cannot be empty or consist only of dots.'))

    # 3. Domain must have a dot and a TLD of at least 2 characters
    if '.' not in domain:
        raise ValidationError(_('Email domain must include a valid extension (e.g. .com, .in).'))

    tld = domain.rsplit('.', 1)[-1]
    if len(tld) < 2:
        raise ValidationError(_('Email domain extension must be at least 2 characters (e.g. .com, .in).'))

    # 4. Disposable domain check
    if domain in _DISPOSABLE_DOMAINS:
        raise ValidationError(_(
            'Temporary or disposable email addresses are not allowed. '
            'Please use a real email address.'
        ))

    # 5. No consecutive dots
    if '..' in email:
        raise ValidationError(_('Email address must not contain consecutive dots.'))

    # 6. Local-part must not start or end with a dot
    if local.startswith('.') or local.endswith('.'):
        raise ValidationError(_('The part before "@" must not start or end with a dot.'))

    return email


class GymRegistrationForm(forms.Form):
    """Form for gym owner registration."""

    name = forms.CharField(max_length=200, label=_('Full Name'))
    email = forms.EmailField(label=_('Email Address'))
    phone = forms.CharField(max_length=20, label=_('Phone Number'))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), label=_('Password'))
    password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), label=_('Confirm Password'))
    gym_name = forms.CharField(max_length=200, label=_('Gym Name'))
    gym_address = forms.CharField(required=False, label=_('Gym Address'), widget=forms.Textarea(attrs={'rows': 3}))

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        # Strict format + disposable-domain validation
        email = _validate_email_strict(email)
        # Uniqueness check
        User = get_user_model()
        if User.objects.filter(email=email).exists():
            raise ValidationError(_('An account with this email address already exists.'))
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        User = get_user_model()
        if User.objects.filter(phone=phone).exists():
            raise ValidationError(_('A user with this phone number already exists.'))
        return phone

    def clean_password_confirm(self):
        password = self.cleaned_data.get('password')
        confirm = self.cleaned_data.get('password_confirm')
        if password and confirm and password != confirm:
            raise ValidationError(_('Passwords do not match.'))
        return confirm

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if password:
            if len(password) < 8:
                raise ValidationError(_('Password must be at least 8 characters long.'))
            if password.isdigit():
                raise ValidationError(_('Password must not be all numbers.'))
        return password


class LoginForm(forms.Form):
    """Login form supporting username, email, or phone."""
    username = forms.CharField(label=_('Email, Phone, or Username'))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}), label=_('Password'))
    remember_me = forms.BooleanField(required=False, label=_('Remember me on this device'))


class PasswordResetForm(forms.Form):
    """Password reset request form."""
    email = forms.EmailField(label=_('Email Address'))

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        # Validate format strictly before looking up the user
        email = _validate_email_strict(email)
        User = get_user_model()
        try:
            self.user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.user = None
        return email


class PasswordResetConfirmForm(forms.Form):
    """Password reset confirmation form."""
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), label=_('New Password'))
    new_password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), label=_('Confirm New Password'))

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        if password:
            if len(password) < 8:
                raise ValidationError(_('Password must be at least 8 characters long.'))
            if password.isdigit():
                raise ValidationError(_('Password must not be all numbers.'))
        return password

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get('new_password')
        confirm = cleaned.get('new_password_confirm')
        if new and confirm and new != confirm:
            raise ValidationError(_('Passwords do not match.'))
        return cleaned


class PasswordChangeForm(forms.Form):
    """Password change form for logged-in users."""
    current_password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}), label=_('Current Password'))
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), label=_('New Password'))
    new_password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), label=_('Confirm New Password'))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data.get('current_password')
        if not self.user.check_password(current):
            raise ValidationError(_('Current password is incorrect.'))
        return current

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        if password:
            if len(password) < 8:
                raise ValidationError(_('Password must be at least 8 characters long.'))
            if password.isdigit():
                raise ValidationError(_('Password must not be all numbers.'))
        return password

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get('new_password')
        confirm = cleaned.get('new_password_confirm')
        if new and confirm and new != confirm:
            raise ValidationError(_('Passwords do not match.'))
        return cleaned


class GymSetupForm(forms.Form):
    """Gym profile setup wizard form."""
    timezone = forms.CharField(max_length=64, initial='Asia/Kolkata')
    currency = forms.CharField(max_length=8, initial='INR')
    inactivity_early_warning_days = forms.IntegerField(min_value=1, initial=2)
    inactivity_at_risk_days = forms.IntegerField(min_value=1, initial=3)
    inactivity_high_risk_days = forms.IntegerField(min_value=1, initial=5)
    inactivity_critical_risk_days = forms.IntegerField(min_value=1, initial=7)
    attendance_check_in_window_minutes = forms.IntegerField(min_value=5, initial=120)
    allow_duplicate_daily_checkin = forms.BooleanField(required=False, initial=False)
    require_phone_verification = forms.BooleanField(required=False, initial=True)
    allow_manual_checkin = forms.BooleanField(required=False, initial=True)
    weekly_attendance_goal = forms.IntegerField(min_value=1, initial=3)
    auto_renew_enabled = forms.BooleanField(required=False, initial=False)
