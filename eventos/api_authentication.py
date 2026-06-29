from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class CronometragemAPIUser:
    is_authenticated = True

    def __str__(self):
        return "cronometragem-api"


class CronometragemAPIKeyAuthentication(BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        expected_key = getattr(settings, "CRONOMETRAGEM_API_KEY", "")
        authorization = request.headers.get("Authorization", "")

        if not expected_key:
            raise AuthenticationFailed("API key is not configured.")

        prefix = f"{self.keyword} "
        if not authorization.startswith(prefix):
            raise AuthenticationFailed("Missing API key.")

        provided_key = authorization.removeprefix(prefix).strip()
        if provided_key != expected_key:
            raise AuthenticationFailed("Invalid API key.")

        return CronometragemAPIUser(), None

    def authenticate_header(self, request):
        return self.keyword
