"""
Accounts views: authentication, profile, gym setup wizard.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.forms import (
    GymSetupForm,
    LoginForm,
    PasswordChangeForm,
    PasswordResetConfirmForm,
    PasswordResetForm,
)
from accounts.models import GymMembership
from audit.models import AuditLog
from gyms.services import get_user_gym
from gyms.workspace_otp import (
    OTPVerificationError,
    create_password_reset_otp,
    get_password_reset_otp_status,
    resend_password_reset_otp,
    verify_password_reset_otp,
)


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
    """Send an OTP for password reset without revealing account existence."""
    if request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            try:
                sent = create_password_reset_otp(email=email, request=request)
            except OTPVerificationError as exc:
                form.add_error(None, str(exc))
                return render(request, 'accounts/password_reset.html', {'form': form})
            except Exception:
                sent = False
            # Clear any prior verified-reset state when a new request is made.
            request.session.pop('password_reset_verified_user_id', None)
            request.session.pop('password_reset_verified_at', None)
            if sent:
                messages.success(request, 'We sent a 6-digit verification code to your email.')
                return redirect('password_reset_verify')
            messages.info(request, 'If an account exists for that email, password reset instructions have been sent.')
            return redirect('login')
    else:
        form = PasswordResetForm()
    return render(request, 'accounts/password_reset.html', {'form': form})


def password_reset_verify_view(request):
    """Verify OTP before allowing password reset."""
    status = get_password_reset_otp_status(request=request)
    if request.method == 'GET' and not status:
        messages.info(request, 'Enter your email to receive a verification code.')
        return redirect('password_reset')

    from accounts.forms import GymWorkspaceOTPVerifyForm
    form = GymWorkspaceOTPVerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user = verify_password_reset_otp(
                code=form.cleaned_data['otp_code'],
                request=request,
            )
        except OTPVerificationError as exc:
            messages.error(request, str(exc))
            status = get_password_reset_otp_status(request=request)
            if not status:
                return redirect('password_reset')
        else:
            request.session['password_reset_verified_user_id'] = user.pk
            request.session['password_reset_verified_at'] = str(int(__import__('time').time()))
            request.session.modified = True
            messages.success(request, 'Code verified. Choose a new password.')
            return redirect('password_reset_confirm')

    status = get_password_reset_otp_status(request=request)
    if not status and request.method != 'POST':
        return redirect('password_reset')
    return render(request, 'accounts/password_reset_verify_otp.html', {
        'form': form,
        'masked_email': (status or {}).get('masked_email', ''),
        'expires_in': (status or {}).get('expires_in', 0),
        'otp_from_email': getattr(settings, 'GYM_WORKSPACE_OTP_FROM_EMAIL', 'satyams0478@gmail.com'),
    })


@require_POST
def password_reset_resend_otp(request):
    try:
        resend_password_reset_otp(request=request)
        messages.success(request, 'A new verification code was sent to your email.')
    except OTPVerificationError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, 'We could not resend the verification code right now. Please try again.')
    status = get_password_reset_otp_status(request=request)
    if not status:
        return redirect('password_reset')
    return redirect('password_reset_verify')


def password_reset_confirm_view(request):
    """Allow the verified account owner to set a new password."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    verified_user_id = request.session.get('password_reset_verified_user_id')
    verified_at = request.session.get('password_reset_verified_at')
    user = None
    try:
        # Verified reset state is short-lived (15 minutes) and single-use.
        if verified_user_id and verified_at and (int(__import__('time').time()) - int(verified_at)) <= 900:
            user = User.objects.filter(pk=verified_user_id, is_active=True).first()
    except Exception:
        user = None
    if not user:
        request.session.pop('password_reset_verified_user_id', None)
        request.session.pop('password_reset_verified_at', None)
        messages.error(request, 'Your password reset session is invalid or has expired. Request a new code.')
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
                request.session.pop('password_reset_verified_user_id', None)
                request.session.pop('password_reset_verified_at', None)
                AuditLog.log_action(
                    actor=user,
                    action='update',
                    metadata={'event': 'password_reset_completed'},
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
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