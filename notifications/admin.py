from django.contrib import admin
from notifications.models import Notification, NotificationType

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'gym', 'notification_type', 'title', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'gym')
    search_fields = ('recipient__username', 'title')
    date_hierarchy = 'created_at'

admin.site.register(NotificationType)