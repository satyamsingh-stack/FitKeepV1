"""
Notifications views: list, mark as read.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponseNotAllowed

from notifications.models import Notification
from notifications.services import get_unread_count


@login_required
def notification_list(request):
    """List notifications for the user."""
    notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:50]
    unread_count = get_unread_count(request.user)

    context = {
        'notifications': notifications,
        'unread_count': unread_count,
    }
    return render(request, 'notifications/list.html', context)


@login_required
def notification_mark_read(request, notification_id):
    """Mark a notification as read."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.mark_as_read()
    return redirect('notification_list')


@login_required
def notification_mark_all_read(request):
    """Mark all notifications as read."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from notifications.services import mark_all_read
    mark_all_read(request.user)
    return redirect('notification_list')