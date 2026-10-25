from django.contrib import admin
from audit.models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('actor', 'gym', 'action', 'target_model', 'target_repr', 'timestamp')
    list_filter = ('action', 'gym')
    search_fields = ('actor__username', 'target_repr')
    date_hierarchy = 'timestamp'
    readonly_fields = ('actor', 'gym', 'action', 'target_model', 'target_id', 'target_repr', 'metadata', 'ip_address', 'timestamp')