from notifications.services import get_unread_count


def unread_notification_count(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0}
    return {'unread_notification_count': get_unread_count(request.user)}
