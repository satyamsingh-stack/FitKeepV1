"""
Forms for member profiles.
"""
from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from members.models import MemberProfile


def normalize_phone_number(val):
    """Normalize Indian phone numbers to standard format (+91XXXXXXXXXX)."""
    if not val:
        return ''
    clean = str(val).strip()
    digits = ''.join(filter(str.isdigit, clean))
    if len(digits) == 10:
        return f'+91{digits}'
    elif len(digits) == 11 and digits.startswith('0'):
        return f'+91{digits[1:]}'
    elif len(digits) == 12 and digits.startswith('91'):
        return f'+{digits}'
    elif clean.startswith('+'):
        return clean.replace(' ', '')
    elif digits:
        return f'+91{digits}'
    return clean


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
        if not self.is_bound and not self.instance.pk:
            self.fields['phone'].initial = '+91 '
            self.fields['emergency_contact'].initial = '+91 '

        self.fields['emergency_contact'].required = True
        self.fields['emergency_contact_name'].required = True
        self.fields['emergency_contact'].label = _('Emergency Contact Phone')
        self.fields['emergency_contact_name'].label = _('Emergency Contact Name')
        self.fields['phone'].widget.attrs.update({'placeholder': '+91 9876543210', 'inputmode': 'tel'})
        self.fields['emergency_contact_name'].widget.attrs.update({
            'placeholder': 'e.g. Ramesh Sharma (Parent / Spouse / Friend)',
        })
        self.fields['emergency_contact'].widget.attrs.update({
            'placeholder': '+91 9876543210',
            'inputmode': 'tel',
        })
        for field_name, field in self.fields.items():
            if not field.required and not isinstance(field, forms.FileField):
                field.required = False

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        digits = ''.join(filter(str.isdigit, phone))
        subscriber_digits = digits[2:] if digits.startswith('91') else (digits[1:] if digits.startswith('0') else digits)
        if not subscriber_digits:
            raise forms.ValidationError(_('Enter a valid 10-digit phone number.'))
        if len(subscriber_digits) < 10:
            raise forms.ValidationError(_('Phone number must have at least 10 digits.'))
        return normalize_phone_number(phone)

    def clean_emergency_contact(self):
        contact = (self.cleaned_data.get('emergency_contact') or '').strip()
        digits = ''.join(filter(str.isdigit, contact))
        subscriber_digits = digits[2:] if digits.startswith('91') else (digits[1:] if digits.startswith('0') else digits)
        if not subscriber_digits:
            raise forms.ValidationError(_('Emergency contact phone is required.'))
        if len(subscriber_digits) < 10:
            raise forms.ValidationError(_('Emergency contact phone must have at least 10 digits.'))
        return normalize_phone_number(contact)

    def clean_emergency_contact_name(self):
        name = (self.cleaned_data.get('emergency_contact_name') or '').strip()
        if not name:
            raise forms.ValidationError(_('Emergency contact name is required.'))
        return name


class MemberOnboardingForm(forms.ModelForm):
    """
    One-step onboarding: member details + membership + first payment.

    The membership and payment sections are optional, so a gym can still add a
    member first and sell a plan later. If a plan is chosen, the end date and fee
    are filled in automatically (and can still be changed for special cases).
    """

    plan = forms.ModelChoiceField(
        queryset=None, required=False, empty_label=None, label=_('Plan'),
    )
    start_date = forms.DateField(required=False, label=_('Starts on'), widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, label=_('Valid until'), widget=forms.DateInput(attrs={'type': 'date'}))
    total_amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, label=_('Fee (\u20b9)'))
    discount_amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, label=_('Discount (\u20b9)'))
    amount_paid = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, label=_('Paid now (\u20b9)'))
    payment_method = forms.ChoiceField(required=False, label=_('Payment method'))
    reference_number = forms.CharField(max_length=100, required=False, label=_('Reference no.'))

    class Meta:
        model = MemberProfile
        fields = [
            'full_name', 'phone', 'email', 'date_of_birth', 'gender',
            'emergency_contact_name', 'emergency_contact', 'address', 'photo', 'notes',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 2}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        from attendance.services import get_gym_local_date
        from memberships.models import MembershipPlan
        from payments.models import Payment

        self.gym = gym
        self.fields['plan'].queryset = (
            MembershipPlan.objects.filter(gym=gym, is_active=True).order_by('price')
            if gym else MembershipPlan.objects.none()
        )
        self.fields['payment_method'].choices = Payment.METHOD_CHOICES
        self.fields['payment_method'].initial = 'cash'
        if not self.is_bound and not self.instance.pk:
            self.fields['phone'].initial = '+91 '
            self.fields['emergency_contact'].initial = '+91 '
        if gym and not self.is_bound:
            self.fields['start_date'].initial = get_gym_local_date(gym)
            self.fields['discount_amount'].initial = 0
            self.fields['amount_paid'].initial = 0

        self.fields['phone'].required = True
        self.fields['phone'].help_text = _('Used for member login and check-in.')
        self.fields['emergency_contact'].required = True
        self.fields['emergency_contact_name'].required = True
        self.fields['emergency_contact'].label = _('Emergency Contact Phone')
        self.fields['emergency_contact_name'].label = _('Emergency Contact Name')
        self.fields['full_name'].widget.attrs.update({'autofocus': True, 'placeholder': 'e.g. Priya Sharma'})
        self.fields['phone'].widget.attrs.update({'placeholder': '+91 9876543210', 'inputmode': 'tel'})
        self.fields['emergency_contact_name'].widget.attrs.update({
            'placeholder': 'e.g. Ramesh Sharma (Parent / Spouse / Friend)',
        })
        self.fields['emergency_contact'].widget.attrs.update({
            'placeholder': '+91 9876543210',
            'inputmode': 'tel',
        })

        for name, field in self.fields.items():
            if name == 'plan':
                continue
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs['class'] = css

    def clean_emergency_contact(self):
        contact = (self.cleaned_data.get('emergency_contact') or '').strip()
        digits = ''.join(filter(str.isdigit, contact))
        subscriber_digits = digits[2:] if digits.startswith('91') else (digits[1:] if digits.startswith('0') else digits)
        if not subscriber_digits:
            raise forms.ValidationError(_('Emergency contact phone is required.'))
        if len(subscriber_digits) < 10:
            raise forms.ValidationError(_('Emergency contact phone must have at least 10 digits.'))
        return normalize_phone_number(contact)

    def clean_emergency_contact_name(self):
        name = (self.cleaned_data.get('emergency_contact_name') or '').strip()
        if not name:
            raise forms.ValidationError(_('Emergency contact name is required.'))
        return name

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        digits = ''.join(filter(str.isdigit, phone))
        subscriber_digits = digits[2:] if digits.startswith('91') else (digits[1:] if digits.startswith('0') else digits)
        if not subscriber_digits:
            raise forms.ValidationError(_('Enter a valid 10-digit phone number.'))
        if len(subscriber_digits) < 10:
            raise forms.ValidationError(_('Phone number must have at least 10 digits.'))
        phone = normalize_phone_number(phone)
        last10 = subscriber_digits[-10:]
        if self.gym:
            from django.db.models import Q
            if MemberProfile.objects.filter(gym=self.gym).filter(
                Q(phone=phone) | Q(phone=last10) | Q(phone=f'0{last10}') | Q(phone=f'+91{last10}')
            ).exists():
                raise forms.ValidationError(_('A member with this phone number already exists in your gym.'))
        return phone

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            return email

        from django.contrib.auth import get_user_model
        UserModel = get_user_model()
        existing_account = UserModel.objects.filter(username=self.cleaned_data.get('phone')).first()
        if existing_account:
            return email
        if UserModel.objects.filter(email__iexact=email).exists():
            self.fields['email'].widget.attrs.update({
                'class': 'form-control is-invalid',
                'aria-invalid': 'true',
            })
            raise forms.ValidationError(
                _('This email is already linked to another account. Use a different email address or leave it blank.')
            )
        return email

    def clean(self):
        cleaned = super().clean()
        plan = cleaned.get('plan')
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        total = cleaned.get('total_amount')
        discount = cleaned.get('discount_amount') or Decimal('0')
        paid = cleaned.get('amount_paid') or Decimal('0')
        has_membership = bool(plan or end or total is not None)

        if not has_membership:
            if paid > 0:
                self.add_error('amount_paid', _('Choose a plan (or enter a fee) before recording a payment.'))
            return cleaned

        if not start:
            self.add_error('start_date', _('Choose when the membership starts.'))
            return cleaned
        if plan:
            if not end:
                from members.services import default_end_date
                end = default_end_date(start, plan.duration_days)
            if total is None:
                total = plan.price
        if end is None:
            self.add_error('end_date', _('Choose the last valid date for this membership.'))
        elif end < start:
            self.add_error('end_date', _('End date must be on or after the start date.'))
        if total is None:
            self.add_error('total_amount', _('Enter the membership fee.'))
        else:
            if discount > total:
                self.add_error('discount_amount', _('Discount cannot exceed the fee.'))
            elif paid > total - discount:
                self.add_error('amount_paid', _('Amount paid cannot exceed the amount due (\u20b9%(due)s).') % {'due': total - discount})
        if not cleaned.get('payment_method'):
            cleaned['payment_method'] = 'cash'

        cleaned['end_date'] = end
        cleaned['total_amount'] = total
        cleaned['discount_amount'] = discount
        cleaned['amount_paid'] = paid
        return cleaned
