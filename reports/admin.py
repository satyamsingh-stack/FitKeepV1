from django.contrib import admin
from reports.models import DashboardMetric

@admin.register(DashboardMetric)
class DashboardMetricAdmin(admin.ModelAdmin):
    list_display = ('gym', 'metric_name', 'computed_at')
    list_filter = ('gym',)