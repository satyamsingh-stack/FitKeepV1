"""
Gym views: tenant management, settings, and platform admin.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.forms import GymSetupForm, GymRegistrationForm
from accounts.models import GymMembership, GymSettings
from audit.models import AuditLog
from gyms.models import Gym
from gyms.services import (
    can_access_gym,
    can_manage_members,
    get_user_gym,
    get_user_role,
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


def register_gym(request):
    """Gym owner registration."""
    if request.method == 'POST':
        form = GymRegistrationForm(request.POST)
        if form.is_valid():
            from django.contrib.auth import get_user_model
            from django.db import transaction

            User = get_user_model()
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data['email'],
                    email=form.cleaned_data['email'],
                    phone=form.cleaned_data['phone'],
                    password=form.cleaned_data['password'],
                    role='gym_owner',
                )
                gym = Gym.objects.create(
                    name=form.cleaned_data['gym_name'],
                    address=form.cleaned_data['gym_address'],
                    phone=form.cleaned_data['phone'],
                    email=form.cleaned_data['email'],
                )
                GymMembership.objects.create(
                    user=user,
                    gym=gym,
                    role='owner',
                    is_primary=True,
                    status='active',
                )
                GymSettings.objects.create(gym=gym)

            AuditLog.log_action(
                actor=user,
                action='create',
                gym=gym,
                target=gym,
                metadata={'registration': 'gym_owner'},
            )
            messages.success(request, 'Gym registered successfully. Please log in.')
            return redirect('login')
    else:
        form = GymRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})


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