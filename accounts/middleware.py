"""
Rate limiting middleware for authentication-sensitive endpoints.
"""
from django.core.cache import cache
from django.http import HttpResponse


class RateLimitMiddleware:
    """Simple rate limiting based on IP address."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in ['/login/', '/password-reset/', '/api/check-in/']:
            ip = self.get_client_ip(request)
            key = f'ratelimit:{request.path}:{ip}'
            limit = 1000
            period = 300  # 5 minutes

            current = cache.get(key)
            if current is not None and current >= limit:
                return HttpResponse('Too many requests. Please try again later.', status=429)

            if current is None:
                cache.set(key, 1, period)
            else:
                cache.set(key, current + 1, period)

        return self.get_response(request)

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip