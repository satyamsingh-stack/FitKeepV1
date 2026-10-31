from django.contrib import admin
from attendance.models import Attendance, AttendanceQRCode

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('member', 'gym', 'check_in_at', 'check_out_at', 'check_in_date', 'source', 'verification_status')
    list_filter = ('gym', 'source', 'verification_status', 'check_in_date')
    search_fields = ('member__full_name', 'member__member_id')
    date_hierarchy = 'check_in_at'

@admin.register(AttendanceQRCode)
class AttendanceQRCodeAdmin(admin.ModelAdmin):
    list_display = ('gym', 'is_active', 'created_at', 'revoked_at')
    list_filter = ('gym', 'is_active')