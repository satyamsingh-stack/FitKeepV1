from django.contrib import admin
from payments.models import Payment, FeeReminder

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('member', 'amount', 'payment_date', 'method', 'status', 'recorded_by')
    list_filter = ('method', 'status', 'member__gym')
    search_fields = ('member__full_name', 'reference_number')
    date_hierarchy = 'payment_date'

@admin.register(FeeReminder)
class FeeReminderAdmin(admin.ModelAdmin):
    list_display = ('member', 'amount_due', 'reminder_date', 'status')
    list_filter = ('status',)