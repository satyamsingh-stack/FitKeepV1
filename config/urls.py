"""
URL configuration for config project.
"""
from django.contrib import admin
from django.urls import include, path
from django.http import HttpResponse
from django.shortcuts import render

from accounts.views import (
    login_view, logout_view, password_reset_view, password_reset_confirm_view, password_change_view,
    profile_view, gym_setup_wizard, super_admin_dashboard,
)
from gyms.views import gym_home, gym_settings_view, register_gym
from members.views import member_list, member_add, member_detail, member_edit, member_check_in, member_reset_password
from attendance.views import (
    attendance_home, check_in, qr_display, qr_generate, attendance_history, attendance_report,
)
from retention.views import retention_list, contact_log_create, contact_history, retention_report
from payments.views import payment_list, payment_add, outstanding_payments, membership_fees
from memberships.views import plan_list, plan_add, plan_edit, subscription_list, subscription_add, subscription_renew
from notifications.views import notification_list, notification_mark_read, notification_mark_all_read
from reports.views import dashboard_view, reports_view
from audit.views import audit_history
from landing_views import landing_view, features_view


def page_not_found_view(request, exception=None, unmatched_path=None):
    return render(request, '404.html', status=404)


urlpatterns = [
    # Public pages
    path('', landing_view, name='landing'),
    path('features/', features_view, name='features'),

    # Auth
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/', register_gym, name='register_gym'),
    path('password-reset/', password_reset_view, name='password_reset'),
    path('password-reset/<uidb64>/<token>/', password_reset_confirm_view, name='password_reset_confirm'),
    path('password-change/', password_change_view, name='password_change'),
    path('profile/', profile_view, name='profile'),

    # Gym setup
    path('setup/', gym_setup_wizard, name='gym_setup'),

    # Platform admin
    path('platform/admin/', super_admin_dashboard, name='super_admin_dashboard'),

    # Gym-scoped URLs
    path('gym/', gym_home, name='gym_home'),
    path('gym/settings/', gym_settings_view, name='gym_settings'),

    # Dashboard & reports
    path('gym/dashboard/', dashboard_view, name='dashboard'),
    path('gym/reports/', reports_view, name='reports'),

    # Members
    path('gym/members/', member_list, name='member_list'),
    path('gym/members/add/', member_add, name='member_add'),
    path('gym/members/<str:member_id>/', member_detail, name='member_detail'),
    path('gym/members/<str:member_id>/edit/', member_edit, name='member_edit'),
    path('gym/members/<str:member_id>/check-in/', member_check_in, name='member_check_in'),
    path('gym/members/<str:member_id>/reset-password/', member_reset_password, name='member_reset_password'),

    # Membership plans
    path('gym/plans/', plan_list, name='plan_list'),
    path('gym/plans/add/', plan_add, name='plan_add'),
    path('gym/plans/<int:plan_id>/edit/', plan_edit, name='plan_edit'),
    path('gym/subscriptions/', subscription_list, name='subscription_list'),
    path('gym/subscriptions/add/', subscription_add, name='subscription_add'),
    path('gym/subscriptions/<int:subscription_id>/renew/', subscription_renew, name='subscription_renew'),

    # Payments
    path('gym/payments/', payment_list, name='payment_list'),
    path('gym/payments/add/', payment_add, name='payment_add'),
    path('gym/payments/outstanding/', outstanding_payments, name='outstanding_payments'),
    path('gym/fees/', membership_fees, name='membership_fees'),

    # Attendance
    path('gym/attendance/', attendance_home, name='attendance_home'),
    path('gym/attendance/history/', attendance_history, name='attendance_history'),
    path('gym/attendance/report/', attendance_report, name='attendance_report'),
    path('gym/attendance/qr/', qr_display, name='qr_display'),
    path('gym/attendance/qr/generate/', qr_generate, name='qr_generate'),
    path('gym/attendance/check-in/', check_in, name='check_in'),

    # Retention
    path('gym/retention/', retention_list, name='retention_list'),
    path('gym/retention/contact/', contact_log_create, name='contact_log_create'),
    path('gym/retention/contact-history/', contact_history, name='contact_history'),
    path('gym/retention/report/', retention_report, name='retention_report'),

    # Notifications
    path('gym/notifications/', notification_list, name='notification_list'),
    path('gym/notifications/<int:notification_id>/read/', notification_mark_read, name='notification_mark_read'),
    path('gym/notifications/mark-all-read/', notification_mark_all_read, name='notification_mark_all_read'),

    # Audit
    path('gym/audit/', audit_history, name='audit_history'),

    # Member portal
    path('member/', include(('accounts.member_urls', 'member'), namespace='member')),

    path('admin/', admin.site.urls),
    path('<path:unmatched_path>', page_not_found_view),
]

handler404 = page_not_found_view
