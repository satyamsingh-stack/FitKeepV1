import re
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from enquiries.forms import EnquiryForm
from enquiries.models import Enquiry
from gyms.services import can_manage_members, get_user_gym, get_user_role
from audit.models import AuditLog


def _whatsapp_url(phone, message):
    number = re.sub(r'\D', '', phone or '')
    if not number:
        return ''
    return f'https://wa.me/{number}?text={quote(message)}'


@login_required
def enquiry_list(request):
    try:
        gym = get_user_gym(request.user)
    except Exception:
        return redirect('login')
    if not can_manage_members(request.user, gym):
        return HttpResponseForbidden('You do not have permission to manage enquiries.')

    editing = None
    if request.method == 'POST':
        enquiry_id = request.POST.get('enquiry_id')
        editing = get_object_or_404(Enquiry, id=enquiry_id, gym=gym) if enquiry_id else None
        form = EnquiryForm(request.POST, instance=editing, gym=gym)
        if form.is_valid():
            enquiry = form.save(commit=False)
            enquiry.gym = gym
            if not editing:
                enquiry.created_by = request.user
            enquiry.save()
            AuditLog.log_action(
                actor=request.user,
                action='update' if editing else 'create',
                gym=gym,
                target=enquiry,
            )
            messages.success(request, 'Enquiry updated.' if editing else 'Enquiry added.')
            return redirect('enquiry_list')
    else:
        edit_id = request.GET.get('edit')
        if edit_id:
            editing = get_object_or_404(Enquiry, id=edit_id, gym=gym)
        form = EnquiryForm(instance=editing, gym=gym)

    enquiries = list(
        Enquiry.objects.filter(gym=gym).select_related('interested_plan').order_by('follow_up_date', '-created_at')
    )
    for enquiry in enquiries:
        whatsapp_number = enquiry.whatsapp_number
        enquiry.welcome_whatsapp_url = _whatsapp_url(
            whatsapp_number,
            f"Hi {enquiry.name}, welcome to {gym.name}! Thanks for enquiring with us. How can we help you get started?",
        )

    context = {
        'gym': gym,
        'form': form,
        'editing': editing,
        'enquiries': enquiries,
        'user_role': get_user_role(request.user, gym),
    }
    return render(request, 'enquiries/list.html', context)
