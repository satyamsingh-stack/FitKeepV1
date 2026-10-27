from django import forms

from enquiries.models import Enquiry


class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ['name', 'phone', 'comments']
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, gym, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['comments'].required = False
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select' if isinstance(field, forms.ChoiceField) else 'form-control'
