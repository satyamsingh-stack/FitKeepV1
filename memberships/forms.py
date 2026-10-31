"""
Forms for memberships, members, payments, and retention.
"""
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from members.models import MemberProfile
from memberships.models import MembershipPlan, MemberSubscription
from payments.models import Payment
from retention.models import ContactLog


class MemberForm(forms.ModelForm):
    """Form for creating and editing member profiles."""

    class Meta:
        model = MemberProfile
        fields = [
            'full_name', 'phone', 'email', 'date_of_birth', 'gender',
            'emergency_contact', 'emergency_contact_name', 'address',
            'photo', 'status', 'notes',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 3}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if not field.required and not isinstance(field, forms.FileField):
                field.required = False


class MembershipPlanForm(forms.ModelForm):
    """Form for creating and editing membership plans."""

    class Meta:
        model = MembershipPlan
        fields = ['name', 'duration', 'duration_days', 'price', 'description', 'is_active']
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'is_active':
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'


class SubscriptionForm(forms.ModelForm):
    """Form for creating subscriptions with plan pricing and custom-plan support."""

    total_amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, label=_('Membership fee'))
    member = forms.ModelChoiceField(
        queryset=MemberProfile.objects.none(),
        label=_('Member'),
        empty_label=_('Select Member'),
    )
    plan = forms.ModelChoiceField(
        queryset=MembershipPlan.objects.none(),
        label=_('Plan'),
        empty_label=_('Select Plan'),
        required=False,
    )

    class Meta:
        model = MemberSubscription
        fields = ['start_date', 'end_date', 'total_amount', 'discount_amount', 'notes']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        if gym:
            self.fields['member'].queryset = MemberProfile.objects.filter(gym=gym, status='active')
            self.fields['plan'].queryset = MembershipPlan.objects.filter(gym=gym, is_active=True)

    def clean(self):
        cleaned = super().clean()
        member = cleaned.get('member')
        plan = cleaned.get('plan')
        total = cleaned.get('total_amount')
        discount = cleaned.get('discount_amount') or 0
        if (total is None or total == 0) and plan:
            cleaned['total_amount'] = total = plan.price
        if total is None:
            self.add_error('total_amount', _('Enter a membership fee or choose a plan with a price.'))
        elif discount > total:
            self.add_error('discount_amount', _('Discount cannot exceed the membership fee.'))
        start_date, end_date = cleaned.get('start_date'), cleaned.get('end_date')
        if start_date and end_date and end_date < start_date:
            self.add_error('end_date', _('End date must be on or after the start date.'))
        if member and start_date and end_date and end_date >= start_date:
            from django.core.exceptions import ValidationError
            from memberships.models import MemberSubscription
            candidate = MemberSubscription(
                member=member,
                gym=member.gym,
                start_date=start_date,
                end_date=end_date,
            )
            try:
                candidate.clean()
            except ValidationError as exc:
                self.add_error('start_date', exc.message_dict.get('start_date', exc.messages))
        return cleaned


class SubscriptionEditForm(forms.ModelForm):
    """Edit the terms and status of an existing membership."""

    class Meta:
        model = MemberSubscription
        fields = ['plan', 'start_date', 'end_date', 'total_amount', 'discount_amount', 'status', 'notes']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plan'].queryset = MembershipPlan.objects.filter(gym=gym).order_by('name')
        self.fields['plan'].required = False
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get('start_date')
        end_date = cleaned.get('end_date')
        total_amount = cleaned.get('total_amount')
        discount_amount = cleaned.get('discount_amount') or 0
        if start_date and end_date and end_date < start_date:
            self.add_error('end_date', _('End date must be on or after the start date.'))
        if total_amount is not None and discount_amount > total_amount:
            self.add_error('discount_amount', _('Discount cannot exceed the membership fee.'))
        if start_date and end_date and end_date >= start_date:
            try:
                self.instance.start_date = start_date
                self.instance.end_date = end_date
                self.instance.clean()
            except ValidationError as exc:
                self.add_error('start_date', exc.message_dict.get('start_date', exc.messages))
        return cleaned


class SubscriptionRenewalForm(forms.Form):
    """Choose the plan, dates, and payment details for a membership renewal."""

    plan = forms.ModelChoiceField(
        queryset=MembershipPlan.objects.none(),
        required=False,
        empty_label=_('Keep current plan'),
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    total_amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    discount_amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, initial=0)
    amount_paid = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, initial=0)
    payment_method = forms.ChoiceField(choices=Payment.METHOD_CHOICES)
    reference_number = forms.CharField(max_length=100, required=False)

    def __init__(self, *args, gym, current_plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        plans = MembershipPlan.objects.filter(gym=gym, is_active=True)
        if current_plan and not current_plan.is_active:
            plans = plans | MembershipPlan.objects.filter(gym=gym, pk=current_plan.pk)
        self.fields['plan'].queryset = plans.order_by('name')
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get('start_date')
        end_date = cleaned.get('end_date')
        total_amount = cleaned.get('total_amount')
        discount_amount = cleaned.get('discount_amount')
        amount_paid = cleaned.get('amount_paid')
        if start_date and end_date and end_date < start_date:
            self.add_error('end_date', _('End date must be on or after the start date.'))
        if total_amount is not None and discount_amount is not None:
            if discount_amount > total_amount:
                self.add_error('discount_amount', _('Discount cannot exceed the membership fee.'))
            elif amount_paid is not None and amount_paid > total_amount - discount_amount:
                self.add_error('amount_paid', _('Amount paid cannot exceed the amount due.'))
        return cleaned


class PaymentForm(forms.ModelForm):
    """Form for recording payments."""

    class Meta:
        model = Payment
        fields = ['member', 'subscription', 'amount', 'method', 'reference_number', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        if gym:
            self.fields['member'].queryset = MemberProfile.objects.filter(gym=gym)
            self.fields['subscription'].queryset = MemberSubscription.objects.filter(gym=gym)


class ContactLogForm(forms.ModelForm):
    """Form for logging member contacts."""

    class Meta:
        model = ContactLog
        fields = ['member', 'contact_outcome', 'notes', 'next_follow_up_date']
        widgets = {
            'contact_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'next_follow_up_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        if gym:
            self.fields['member'].queryset = MemberProfile.objects.filter(gym=gym, status='active')