"""
Forms for authentication and gym onboarding.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from accounts.models import GymMembership
from gyms.models import Gym


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
        email = self.cleaned_data.get('email')
        User = get_user_model()
        if User.objects.filter(email=email).exists():
            raise ValidationError(_('A user with this email already exists.'))
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
        email = self.cleaned_data.get('email')
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