from django.contrib import admin
from memberships.models import MembershipPlan, MemberSubscription

@admin.register(MembershipPlan)
class MembershipPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'gym', 'duration', 'price', 'is_active')
    list_filter = ('gym', 'duration', 'is_active')
    search_fields = ('name',)

@admin.register(MemberSubscription)
class MemberSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('member', 'plan', 'start_date', 'end_date', 'status', 'amount_paid')
    list_filter = ('status', 'gym')
    search_fields = ('member__full_name',)
    date_hierarchy = 'start_date'