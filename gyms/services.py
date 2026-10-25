"""
Core service module for tenant isolation and gym access control.
"""
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404

from gyms.models import Gym
from accounts.models import GymMembership, User


class GymAccessError(Exception):
    """Raised when a user attempts to access a gym they don't have access to."""
    pass


def get_user_gym(user):
    """
    Returns the primary gym for the given user.
    Raises GymAccessError if user has no active gym membership.
    """
    if not user.is_authenticated:
        raise GymAccessError('User is not authenticated')

    if user.is_super_admin:
        return None  # Super admin can access any gym

    membership = user.gym_memberships.filter(is_primary=True, status='active').first()
    if not membership:
        membership = user.gym_memberships.filter(status='active').first()
    if not membership:
        raise GymAccessError('User has no active gym membership')
    return membership.gym


def get_user_role(user, gym=None):
    """Returns the user's role at the specified gym."""
    if not user.is_authenticated:
        return None
    if user.is_super_admin:
        return 'super_admin'
    if gym is None:
        gym = get_user_gym(user)
        if gym is None:
            return None
    membership = user.gym_memberships.filter(gym=gym, status='active').first()
    return membership.role if membership else None


def can_access_gym(user, gym):
    """Check if user can access the specified gym."""
    if not user.is_authenticated:
        return False
    if user.is_super_admin:
        return True
    return user.gym_memberships.filter(gym=gym, status='active').exists()


def can_manage_members(user, gym):
    """Check if user can manage members (owner or staff)."""
    if not user.is_authenticated:
        return False
    if user.is_super_admin:
        return True
    membership = user.gym_memberships.filter(gym=gym, status='active').first()
    if not membership:
        return False
    return membership.role in ('owner', 'staff')


def can_view_reports(user, gym):
    """Check if user can view reports (owner or staff)."""
    return can_manage_members(user, gym)


def get_gym_from_request(request):
    """Extract gym from URL kwargs or session."""
    gym_id = request.resolver_kwargs.get('gym_id')
    if gym_id:
        return get_object_or_404(Gym, pk=gym_id)
    gym_slug = request.resolver_kwargs.get('gym_slug')
    if gym_slug:
        return get_object_or_404(Gym, slug=gym_slug)
    return get_user_gym(request.user)


def require_gym_access(view_func):
    """Decorator to ensure user has access to the gym in the URL."""
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        gym_id = kwargs.get('gym_id')
        if gym_id:
            gym = get_object_or_404(Gym, pk=gym_id)
            if not can_access_gym(request.user, gym):
                return HttpResponseForbidden('Access denied to this gym.')
        return view_func(request, *args, **kwargs)
    return wrapper


def require_staff_access(view_func):
    """Decorator to ensure user has staff-level access to the gym."""
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        gym_id = kwargs.get('gym_id')
        if gym_id:
            gym = get_object_or_404(Gym, pk=gym_id)
            if not can_manage_members(request.user, gym):
                return HttpResponseForbidden('You do not have permission to access this page.')
        return view_func(request, *args, **kwargs)
    return wrapper


def get_member_by_phone(gym, phone):
    """Look up a member by phone number within a gym."""
    if not phone:
        return None
    from members.models import MemberProfile
    return MemberProfile.objects.filter(gym=gym, phone=phone).first()


def get_member_by_id(gym, member_id):
    """Look up a member by member_id within a gym."""
    from members.models import MemberProfile
    return get_object_or_404(MemberProfile, gym=gym, member_id=member_id)


def get_active_subscription(member):
    """Get the member's active subscription, if any."""
    from django.utils import timezone
    from attendance.services import get_gym_local_date
    from memberships.services import sync_subscription_statuses
    sync_subscription_statuses(member=member)
    today = get_gym_local_date(member.gym)
    return member.subscriptions.filter(status='active', start_date__lte=today, end_date__gte=today).order_by('-end_date').first()