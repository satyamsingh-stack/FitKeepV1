"""
Member portal URLs.
"""
from django.urls import path
from django.contrib.auth import views as auth_views

from accounts import member_views

app_name = 'member'

urlpatterns = [
    path('', member_views.member_dashboard, name='dashboard'),
    path('check-in/', member_views.member_check_in, name='check_in'),
    path('check-in/summary/', member_views.member_check_in_summary, name='check_in_summary'),
    path('attendance/', member_views.member_attendance_history, name='attendance_history'),
    path('calendar/', member_views.member_attendance_calendar, name='attendance_calendar'),
    path('streaks/', member_views.member_streaks, name='streaks'),
    path('achievements/', member_views.member_achievements, name='achievements'),
    path('membership/', member_views.member_membership, name='membership'),
    path('payments/', member_views.member_payments, name='payments'),
    path('profile/', member_views.member_profile, name='profile'),
]