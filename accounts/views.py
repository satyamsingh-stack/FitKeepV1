"""
Accounts views: authentication, profile, gym setup wizard.
"""
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.forms import (
    LoginForm,
    PasswordResetForm,
    PasswordResetConfirmForm,
    PasswordChangeForm,
    GymSetupForm,
)
from accounts.models import GymMembership
from audit.models import AuditLog
from gyms.models import Gym
from gyms.services import get_user_gym


def login_view(request):
    """Login view supporting username, email, or phone."""
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            from django.contrib.auth import authenticate
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            remember = form.cleaned_data.get('remember_me', False)

            user = authenticate(request, username=username, password=password)
            if user is not None:
                auth_login(request, user)
                if not remember:
                    request.session.set_expiry(0)

                AuditLog.log_action(
                    actor=user,
                    action='login',
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
                next_url = request.GET.get('next')
                if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                    return HttpResponseRedirect(next_url)
                if getattr(user, 'is_super_admin', False):
                    return redirect('super_admin_dashboard')
                if getattr(user, 'role', '') == 'gym_member':
                    return redirect('member:dashboard')
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid credentials. Please try again.')
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    """Logout view. State changes are accepted only through POST."""
    if request.method != 'POST':
        return HttpResponseForbidden('Use the sign-out form to log out.')
    AuditLog.log_action(
        actor=request.user if request.user.is_authenticated else None,
        action='logout',
        ip_address=request.META.get('REMOTE_ADDR'),
    )
    auth_logout(request)
    return redirect('landing')


def password_reset_view(request):
    """Send a time-limited password reset link without revealing account existence."""
    if request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            user = form.user
            if user and user.is_active and user.email:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                reset_url = request.build_absolute_uri(reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token}))
                try:
                    send_mail(
                        subject='Reset your FitKeep password',
                        message=f'Use this link to reset your FitKeep password. If you did not request this, ignore this email.\n\n{reset_url}',
                        from_email=None,
                        recipient_list=[user.email],
                        fail_silently=False,
                    )
                except Exception:
                    # Keep the response generic to avoid disclosing account existence.
                    pass
            messages.info(request, 'If an account exists for that email, password reset instructions have been sent.')
            return redirect('login')
    else:
        form = PasswordResetForm()
    return render(request, 'accounts/password_reset.html', {'form': form})


def password_reset_confirm_view(request, uidb64, token):
    """Validate a signed reset token and allow the account owner to set a new password."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid, is_active=True)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if not user or not default_token_generator.check_token(user, token):
        messages.error(request, 'This password reset link is invalid or has expired. Request a new one.')
        return redirect('password_reset')

    if request.method == 'POST':
        form = PasswordResetConfirmForm(request.POST)
        if form.is_valid():
            try:
                validate_password(form.cleaned_data['new_password'], user=user)
            except Exception as exc:
                form.add_error('new_password', exc)
            else:
                user.set_password(form.cleaned_data['new_password'])
                user.save(update_fields=['password'])
                messages.success(request, 'Your password has been reset. You can now sign in.')
                return redirect('login')
    else:
        form = PasswordResetConfirmForm()
    return render(request, 'accounts/password_reset_confirm.html', {'form': form})


def password_change_view(request):
    """Password change for logged-in users."""
    if not request.user.is_authenticated:
        return redirect('login')

    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            try:
                validate_password(form.cleaned_data['new_password'], user=request.user)
            except Exception as exc:
                form.add_error('new_password', exc)
                return render(request, 'accounts/password_change.html', {'form': form})
            request.user.set_password(form.cleaned_data['new_password'])
            request.user.save(update_fields=['password'])
            update_session_auth_hash(request, request.user)

            AuditLog.log_action(
                actor=request.user,
                action='password_change',
            )
            messages.success(request, 'Password changed successfully.')
            return redirect('profile')
    else:
        form = PasswordChangeForm(request.user)
    return render(request, 'accounts/password_change.html', {'form': form})


@login_required
def profile_view(request):
    """User profile view."""
    if request.method == 'POST':
        # Update profile fields
        user = request.user
        user.first_name = request.POST.get('first_name', '')
        user.last_name = request.POST.get('last_name', '')
        user.email = request.POST.get('email', user.email)
        user.phone = request.POST.get('phone', user.phone)
        user.date_of_birth = request.POST.get('date_of_birth') or None
        user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('profile')

    context = {
        'user': request.user,
    }
    return render(request, 'accounts/profile.html', context)


def gym_setup_wizard(request):
    """Gym setup wizard for new gym owners."""
    if not request.user.is_authenticated:
        return redirect('login')

    try:
        gym = get_user_gym(request.user)
    except Exception:
        return HttpResponseForbidden('No gym associated with your account.')

    membership = GymMembership.objects.filter(user=request.user, gym=gym).first()
    if not membership or membership.role != 'owner':
        return HttpResponseForbidden('Only gym owners can access setup.')

    if request.method == 'POST':
        form = GymSetupForm(request.POST)
        if form.is_valid():
            from accounts.models import GymSettings
            settings_obj = GymSettings.get_for_gym(gym)
            settings_obj.inactivity_early_warning_days = form.cleaned_data['inactivity_early_warning_days']
            settings_obj.inactivity_at_risk_days = form.cleaned_data['inactivity_at_risk_days']
            settings_obj.inactivity_high_risk_days = form.cleaned_data['inactivity_high_risk_days']
            settings_obj.inactivity_critical_risk_days = form.cleaned_data['inactivity_critical_risk_days']
            settings_obj.attendance_check_in_window_minutes = form.cleaned_data['attendance_check_in_window_minutes']
            settings_obj.allow_duplicate_daily_checkin = form.cleaned_data['allow_duplicate_daily_checkin']
            settings_obj.require_phone_verification = form.cleaned_data['require_phone_verification']
            settings_obj.allow_manual_checkin = form.cleaned_data['allow_manual_checkin']
            settings_obj.weekly_attendance_goal = form.cleaned_data['weekly_attendance_goal']
            settings_obj.auto_renew_enabled = form.cleaned_data['auto_renew_enabled']
            settings_obj.save()

            AuditLog.log_action(
                actor=request.user,
                action='update',
                gym=gym,
                target=settings_obj,
            )
            messages.success(request, 'Gym setup completed successfully.')
            return redirect('gym_home')
    else:
        form = GymSetupForm()

    context = {
        'gym': gym,
        'form': form,
    }
    return render(request, 'accounts/gym_setup.html', context)


def super_admin_dashboard(request):
    """Platform admin dashboard."""
    if not request.user.is_authenticated or not request.user.is_super_admin:
        return HttpResponseForbidden('Access denied.')

    gyms = Gym.objects.all().order_by('-created_at')
    context = {
        'gyms': gyms,
        'total_gyms': gyms.count(),
        'active_gyms': gyms.filter(status='active').count(),
        'trial_gyms': gyms.filter(status='trial').count(),
    }
    return render(request, 'admin/platform_dashboard.html', context)