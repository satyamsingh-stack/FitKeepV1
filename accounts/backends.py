"""
Custom authentication backend supporting email or phone login.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrPhoneBackend(ModelBackend):
    """Authenticate using username, email, or phone."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get('username')
        if username is None or password is None:
            return None

        try:
            # Try username first
            user = UserModel._default_manager.get(username=username)
        except UserModel.DoesNotExist:
            try:
                # Try email
                user = UserModel._default_manager.get(email=username)
            except UserModel.DoesNotExist:
                try:
                    # Try phone
                    user = UserModel._default_manager.get(phone=username)
                except UserModel.DoesNotExist:
                    # Run the default password validator once to reduce timing attacks
                    UserModel().set_password(password)
                    return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None