from django.contrib import admin
from retention.models import RetentionRiskLevel, ContactLog, RetentionMetric

@admin.register(RetentionRiskLevel)
class RetentionRiskLevelAdmin(admin.ModelAdmin):
    list_display = ('gym', 'level', 'min_days', 'max_days', 'is_active')
    list_filter = ('gym', 'level', 'is_active')

@admin.register(ContactLog)
class ContactLogAdmin(admin.ModelAdmin):
    list_display = ('member', 'contact_outcome', 'contact_date', 'next_follow_up_date', 'contacted_by')
    list_filter = ('contact_outcome', 'gym')
    search_fields = ('member__full_name',)

@admin.register(RetentionMetric)
class RetentionMetricAdmin(admin.ModelAdmin):
    list_display = ('gym', 'period_start', 'period_end', 'total_at_risk', 'total_contacted', 'total_returned')
    list_filter = ('gym',)