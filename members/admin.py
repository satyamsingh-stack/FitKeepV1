from django.contrib import admin
from members.models import MemberProfile

@admin.register(MemberProfile)
class MemberProfileAdmin(admin.ModelAdmin):
    list_display = ('member_id', 'full_name', 'gym', 'phone', 'status', 'last_attendance_date')
    list_filter = ('gym', 'status')
    search_fields = ('full_name', 'member_id', 'phone')
    date_hierarchy = 'joined_at'