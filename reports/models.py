from django.db import models


class DashboardMetric(models.Model):
    """Cached dashboard metrics for performance."""

    gym = models.ForeignKey('gyms.Gym', on_delete=models.CASCADE, related_name='dashboard_metrics')
    metric_name = models.CharField(max_length=100)
    metric_value = models.JSONField(default=dict)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gym', 'metric_name'],
                name='unique_dashboard_metric',
            ),
        ]

    def __str__(self):
        return f'{self.gym.name} - {self.metric_name}'