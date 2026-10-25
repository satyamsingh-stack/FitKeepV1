from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import User, GymMembership, GymSettings
from gyms.models import Gym


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'phone', 'role', 'is_platform_admin', 'is_active')
    list_filter = ('role', 'is_platform_admin', 'is_active')
    search_fields = ('username', 'email', 'phone')
    ordering = ('username',)


@admin.register(Gym)
class GymAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'status', 'plan', 'created_at')
    list_filter = ('status', 'plan')
    search_fields = ('name', 'slug')
    date_hierarchy = 'created_at'


admin.site.register(GymMembership)
admin.site.register(GymSettings)