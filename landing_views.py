"""
Landing page views.
"""
from django.shortcuts import render
from django.http import HttpResponse


def landing_view(request):
    """Public landing page showcasing the platform."""
    return render(request, 'landing.html')


def pricing_view(request):
    """Pricing page."""
    return render(request, 'pricing.html')


def features_view(request):
    """Features page."""
    return render(request, 'features.html')