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
        if request.path in [
            '/login/',
            '/password-reset/',
            '/password-reset/verify/',
            '/password-reset/resend-otp/',
            '/password-reset/confirm/',
            '/api/check-in/',
            '/register/',
            '/register/verify/',
            '/register/resend-otp/',
            '/register/fitkeep-verify/',
            '/register/fitkeep-resend-otp/',
            '/gym/workspace/create/',
            '/gym/workspace/verify/',
            '/gym/workspace/resend-otp/',
        ]:
            ip = self.get_client_ip(request)
            key = f'ratelimit:{request.path}:{ip}'
            limits = {
                '/login/': (20, 300),
                '/password-reset/': (10, 300),
                '/password-reset/verify/': (20, 300),
                '/password-reset/resend-otp/': (10, 300),
                '/password-reset/confirm/': (15, 300),
                '/api/check-in/': (1000, 300),
                '/register/': (10, 300),
                '/register/verify/': (20, 300),
                '/register/resend-otp/': (10, 300),
                '/register/fitkeep-verify/': (20, 300),
                '/register/fitkeep-resend-otp/': (10, 300),
                '/gym/workspace/create/': (10, 300),
                '/gym/workspace/verify/': (20, 300),
                '/gym/workspace/resend-otp/': (10, 300),
            }
            limit, period = limits.get(request.path, (20, 300))

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