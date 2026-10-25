"""
Audit views: audit log history.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render

from audit.models import AuditLog
from gyms.services import get_user_gym, get_user_role, can_manage_members


@login_required
def audit_history(request):
    """View audit history for the user's gym."""
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')

    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to view the audit history.')
    logs = AuditLog.objects.filter(gym=gym).order_by('-timestamp')[:100]
    context = {
        'gym': gym,
        'logs': logs,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'audit/history.html', context)