from django import forms

from enquiries.models import Enquiry
from memberships.models import MembershipPlan


class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = [
            'name', 'phone', 'whatsapp_number', 'email', 'interested_plan',
            'details', 'comments', 'follow_up_date', 'status',
        ]
        widgets = {
            'details': forms.Textarea(attrs={'rows': 3}),
            'comments': forms.Textarea(attrs={'rows': 3}),
            'follow_up_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['interested_plan'].queryset = MembershipPlan.objects.filter(
            gym=gym,
        ).order_by('name')
        self.fields['interested_plan'].required = False
        self.fields['whatsapp_number'].required = False
        self.fields['email'].required = False
        self.fields['details'].required = False
        self.fields['comments'].required = False
        self.fields['follow_up_date'].required = False
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select' if isinstance(field, forms.ChoiceField) else 'form-control'
