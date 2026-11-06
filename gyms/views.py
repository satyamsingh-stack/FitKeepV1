"""
Gym views: tenant management, settings, and platform admin.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.forms import (
    GymRegistrationForm,
    GymSetupForm,
    GymWorkspaceCreateForm,
    GymWorkspaceOTPVerifyForm,
)
from accounts.models import GymMembership, GymSettings
from audit.models import AuditLog
from gyms.models import Gym
from gyms.services import (
    can_access_gym,
    can_manage_members,
    get_user_gym,
    get_user_role,
)
from gyms.workspace_otp import (
    OTPVerificationError,
    create_registration_otp,
    create_workspace_otp,
    get_registration_fitkeep_admin_otp_status,
    get_registration_otp_status,
    get_workspace_otp_status,
    resend_registration_fitkeep_admin_otp,
    resend_registration_otp,
    resend_workspace_otp,
    verify_registration_fitkeep_admin_otp,
    verify_registration_otp,
    verify_workspace_otp,
)



def gym_home(request):
    """Gym home page showing overview."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if getattr(request.user, 'role', '') == 'gym_member':
        return redirect('member:dashboard')
    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view the gym workspace.')
    context = {
        'gym': gym,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'gyms/home.html', context)


def gym_settings_view(request):
    """Gym settings page."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to access settings.')

    if request.method == 'POST':
        form = GymSetupForm(request.POST)
        if form.is_valid():
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
                metadata={'updated_fields': 'gym_settings'},
            )
            messages.success(request, 'Settings updated successfully.')
            return redirect('gym_settings')
    else:
        settings_obj = GymSettings.get_for_gym(gym)
        form = GymSetupForm(initial={
            'inactivity_early_warning_days': settings_obj.inactivity_early_warning_days,
            'inactivity_at_risk_days': settings_obj.inactivity_at_risk_days,
            'inactivity_high_risk_days': settings_obj.inactivity_high_risk_days,
            'inactivity_critical_risk_days': settings_obj.inactivity_critical_risk_days,
            'attendance_check_in_window_minutes': settings_obj.attendance_check_in_window_minutes,
            'allow_duplicate_daily_checkin': settings_obj.allow_duplicate_daily_checkin,
            'require_phone_verification': settings_obj.require_phone_verification,
            'allow_manual_checkin': settings_obj.allow_manual_checkin,
            'weekly_attendance_goal': settings_obj.weekly_attendance_goal,
            'auto_renew_enabled': settings_obj.auto_renew_enabled,
        })

    context = {
        'gym': gym,
        'form': form,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'gyms/settings.html', context)


@login_required
def gym_workspace_create(request):
    """Start gym workspace creation by collecting details and sending an OTP."""
    if getattr(request.user, 'role', '') == 'gym_member':
        return HttpResponseForbidden('Members cannot create gym workspaces.')
    if not request.user.email:
        messages.error(request, 'Add an email address to your account before creating a gym workspace.')
        return redirect('profile')

    if request.method == 'POST':
        form = GymWorkspaceCreateForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                create_workspace_otp(user=request.user, gym_data=form.cleaned_data, request=request)
            except OTPVerificationError as exc:
                form.add_error(None, str(exc))
            except Exception:
                form.add_error(None, 'We could not send the verification code right now. Please try again.')
            else:
                messages.success(request, 'We sent a 6-digit verification code to your registered email.')
                return redirect('gym_workspace_verify')
    else:
        initial = {
            'gym_email': request.user.email,
            'gym_phone': getattr(request.user, 'phone', '') or '',
        }
        status = get_workspace_otp_status(user=request.user)
        if status and status.get('gym_name'):
            initial['gym_name'] = status['gym_name']
        form = GymWorkspaceCreateForm(user=request.user, initial=initial)

    return render(request, 'gyms/workspace_create.html', {
        'form': form,
        'current_gym': get_user_gym(request.user) if request.user.is_authenticated else None,
    })


@login_required
def gym_workspace_verify(request):
    """Verify OTP and create the gym workspace only after successful verification."""
    if getattr(request.user, 'role', '') == 'gym_member':
        return HttpResponseForbidden('Members cannot create gym workspaces.')
    status = get_workspace_otp_status(user=request.user)
    if request.method == 'GET' and not status:
        messages.info(request, 'Enter your gym details to receive a verification code.')
        return redirect('gym_workspace_create')

    form = GymWorkspaceOTPVerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            gym = verify_workspace_otp(
                user=request.user,
                code=form.cleaned_data['otp_code'],
                request=request,
            )
        except OTPVerificationError as exc:
            messages.error(request, str(exc))
            status = get_workspace_otp_status(user=request.user)
            if not status:
                return redirect('gym_workspace_create')
        else:
            messages.success(request, f'"{gym.name}" workspace created successfully.')
            return redirect('dashboard')

    status = get_workspace_otp_status(user=request.user)
    if not status:
        return redirect('gym_workspace_create')
    return render(request, 'gyms/workspace_verify_otp.html', {
        'form': form,
        'masked_email': status['masked_email'],
        'gym_name': status['gym_name'],
        'resend_available_in': status['resend_available_in'],
        'expires_in': status['expires_in'],
        'otp_from_email': getattr(settings, 'GYM_WORKSPACE_OTP_FROM_EMAIL', 'satyams0478@gmail.com'),
        'current_gym': get_user_gym(request.user) if request.user.is_authenticated else None,
    })


@login_required
@require_POST
def gym_workspace_resend_otp(request):
    """Resend OTP with cooldown and attempt-safe regeneration."""
    if getattr(request.user, 'role', '') == 'gym_member':
        return HttpResponseForbidden('Members cannot create gym workspaces.')
    try:
        resend_workspace_otp(user=request.user, request=request)
        messages.success(request, 'A new verification code was sent to your registered email.')
    except OTPVerificationError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, 'We could not resend the verification code right now. Please try again.')
    status = get_workspace_otp_status(user=request.user)
    if not status:
        return redirect('gym_workspace_create')
    return redirect('gym_workspace_verify')


def register_gym(request):
    """Gym owner registration with mandatory email OTP verification.

    The owner account and gym workspace are created only after the 6-digit
    code sent to the provided email is verified.
    """
    if request.user.is_authenticated and not getattr(request.user, 'is_super_admin', False):
        messages.info(request, 'Use "Create gym workspace" to add another workspace to your account.')
        return redirect('gym_workspace_create')

    if request.method == 'POST':
        form = GymRegistrationForm(request.POST)
        if form.is_valid():
            try:
                create_registration_otp(registration_data=form.cleaned_data, request=request)
            except OTPVerificationError as exc:
                form.add_error(None, str(exc))
            except Exception:
                form.add_error(None, 'We could not send the verification code right now. Please try again.')
            else:
                messages.success(request, 'We sent a 6-digit verification code to your email. Verify it to create your workspace.')
                return redirect('register_gym_verify')
    else:
        form = GymRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})


def register_gym_verify(request):
    """Verify owner email OTP, then require FitKeep admin verification."""
    if request.user.is_authenticated and not getattr(request.user, 'is_super_admin', False):
        return redirect('gym_workspace_create')
    admin_status = get_registration_fitkeep_admin_otp_status(request=request)
    if admin_status:
        return redirect('register_gym_fitkeep_verify')
    status = get_registration_otp_status(request=request)
    if request.method == 'GET' and not status:
        messages.info(request, 'Enter your details to receive a verification code.')
        return redirect('register_gym')

    form = GymWorkspaceOTPVerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            result = verify_registration_otp(
                code=form.cleaned_data['otp_code'],
                request=request,
            )
        except OTPVerificationError as exc:
            messages.error(request, str(exc))
            admin_status = get_registration_fitkeep_admin_otp_status(request=request)
            if admin_status:
                return redirect('register_gym_fitkeep_verify')
            status = get_registration_otp_status(request=request)
            if not status:
                return redirect('register_gym')
        else:
            if isinstance(result, dict) and result.get('next') == 'fitkeep_admin_verification':
                messages.success(request, 'Your email is verified. Enter the FitKeep admin verification code to complete registration.')
                return redirect('register_gym_fitkeep_verify')
            user, gym = result
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, f'"{gym.name}" workspace created and verified successfully.')
            return redirect('dashboard')

    admin_status = get_registration_fitkeep_admin_otp_status(request=request)
    if admin_status:
        return redirect('register_gym_fitkeep_verify')
    status = get_registration_otp_status(request=request)
    if not status:
        return redirect('register_gym')
    return render(request, 'accounts/register_verify_otp.html', {
        'form': form,
        'masked_email': status['masked_email'],
        'gym_name': status['gym_name'],
        'resend_available_in': status['resend_available_in'],
        'expires_in': status['expires_in'],
        'otp_from_email': getattr(settings, 'GYM_WORKSPACE_OTP_FROM_EMAIL', 'satyams0478@gmail.com'),
        'verification_stage': 'owner_email',
    })


def register_gym_fitkeep_verify(request):
    """Second registration verification: OTP sent to FitKeep admin email."""
    if request.user.is_authenticated and not getattr(request.user, 'is_super_admin', False):
        return redirect('gym_workspace_create')
    status = get_registration_fitkeep_admin_otp_status(request=request)
    if request.method == 'GET' and not status:
        owner_status = get_registration_otp_status(request=request)
        if owner_status:
            return redirect('register_gym_verify')
        messages.info(request, 'Complete gym registration and owner email verification first.')
        return redirect('register_gym')

    form = GymWorkspaceOTPVerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user, gym = verify_registration_fitkeep_admin_otp(
                code=form.cleaned_data['otp_code'],
                request=request,
            )
        except OTPVerificationError as exc:
            messages.error(request, str(exc))
            status = get_registration_fitkeep_admin_otp_status(request=request)
            if not status:
                return redirect('register_gym')
        else:
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, f'"{gym.name}" workspace created after FitKeep admin verification.')
            return redirect('dashboard')

    status = get_registration_fitkeep_admin_otp_status(request=request)
    if not status:
        return redirect('register_gym')
    return render(request, 'accounts/register_verify_fitkeep_otp.html', {
        'form': form,
        'masked_email': status['masked_email'],
        'gym_name': status['gym_name'],
        'resend_available_in': status['resend_available_in'],
        'expires_in': status['expires_in'],
        'otp_from_email': getattr(settings, 'GYM_WORKSPACE_OTP_FROM_EMAIL', 'satyams0478@gmail.com'),
        'verification_stage': 'fitkeep_admin',
    })


@require_POST
def register_gym_fitkeep_resend_otp(request):
    if request.user.is_authenticated and not getattr(request.user, 'is_super_admin', False):
        return redirect('gym_workspace_create')
    try:
        resend_registration_fitkeep_admin_otp(request=request)
        messages.success(request, 'A new FitKeep admin verification code was sent.')
    except OTPVerificationError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, 'We could not resend the FitKeep verification code right now. Please try again.')
    status = get_registration_fitkeep_admin_otp_status(request=request)
    if not status:
        return redirect('register_gym')
    return redirect('register_gym_fitkeep_verify')


@require_POST
def register_gym_resend_otp(request):
    if request.user.is_authenticated and not getattr(request.user, 'is_super_admin', False):
        return redirect('gym_workspace_create')
    try:
        resend_registration_otp(request=request)
        messages.success(request, 'A new verification code was sent to your email.')
    except OTPVerificationError as exc:
        messages.error(request, str(exc))
    except Exception:
        messages.error(request, 'We could not resend the verification code right now. Please try again.')
    status = get_registration_otp_status(request=request)
    if not status:
        return redirect('register_gym')
    return redirect('register_gym_verify')


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