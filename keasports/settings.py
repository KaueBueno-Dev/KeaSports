import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

try:
    import dj_database_url
except ImportError:
    dj_database_url = None


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def unique_list(items):
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def supabase_public_host(host):
    if host.endswith(".storage.supabase.co"):
        return host.replace(".storage.supabase.co", ".supabase.co")
    return host


def supabase_s3_host(host):
    if host.endswith(".supabase.co") and not host.endswith(".storage.supabase.co"):
        return host.replace(".supabase.co", ".storage.supabase.co")
    return host


DEBUG = env_bool("DJANGO_DEBUG", False)
MEDIA_STORAGE = os.getenv("DJANGO_MEDIA_STORAGE", "local").strip().lower()
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "local-development-key-change-me"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "127.0.0.1,localhost" if DEBUG else "",
)
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
ALLOWED_HOSTS = unique_list(ALLOWED_HOSTS)
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")
CSRF_TRUSTED_ORIGINS = unique_list(CSRF_TRUSTED_ORIGINS)


INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "eventos.apps.EventosConfig",
    "rest_framework",
    "django_filters",
    "widget_tweaks",
]
if MEDIA_STORAGE == "s3":
    INSTALLED_APPS.append("storages")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "keasports.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "keasports.wsgi.application"


DB_CONN_MAX_AGE = env_int("DB_CONN_MAX_AGE", 60)
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    if dj_database_url is None:
        raise ImproperlyConfigured("dj-database-url must be installed to use DATABASE_URL.")

    DATABASES = {
        "default": dj_database_url.config(
            conn_max_age=DB_CONN_MAX_AGE,
            conn_health_checks=True,
            ssl_require=env_bool("DB_SSL_REQUIRE", False),
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": os.getenv("DB_ENGINE", "django.db.backends.postgresql"),
            "NAME": os.getenv("DB_NAME", "KeaBase" if DEBUG else ""),
            "USER": os.getenv("DB_USER", "postgres" if DEBUG else ""),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost" if DEBUG else ""),
            "PORT": os.getenv("DB_PORT", "5433" if DEBUG else ""),
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }
    }

    if not DEBUG and not DATABASES["default"]["NAME"]:
        raise ImproperlyConfigured("DB_NAME or DATABASE_URL must be set in production.")


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True


STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "keasports" / "static",
]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

if MEDIA_STORAGE == "s3":
    required_s3_env = [
        "AWS_STORAGE_BUCKET_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_S3_REGION_NAME",
    ]
    missing_s3_env = [name for name in required_s3_env if not os.getenv(name)]
    if missing_s3_env:
        raise ImproperlyConfigured(
            "Missing required S3 media settings: " + ", ".join(missing_s3_env)
        )

    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip()
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "").strip()
    SUPABASE_STORAGE_URL = os.getenv("SUPABASE_STORAGE_URL", "").strip().rstrip("/")
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL", "").strip().rstrip("/")
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN", "").strip()
    AWS_LOCATION = os.getenv("AWS_LOCATION", "media").strip().strip("/")
    AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path").strip() or "path"
    AWS_S3_SIGNATURE_VERSION = os.getenv("AWS_S3_SIGNATURE_VERSION", "s3v4").strip()
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = env_bool("AWS_QUERYSTRING_AUTH", False)
    AWS_S3_FILE_OVERWRITE = env_bool("AWS_S3_FILE_OVERWRITE", False)
    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": os.getenv("AWS_S3_CACHE_CONTROL", "max-age=86400"),
    }

    # Supabase exposes two different URL shapes:
    # - the S3-compatible endpoint is only for boto3 uploads/signatures;
    # - the public object URL is what browsers must receive for ImageField/FileField URLs.
    if SUPABASE_STORAGE_URL:
        parsed_supabase_url = urlparse(SUPABASE_STORAGE_URL)
        supabase_host = parsed_supabase_url.netloc or parsed_supabase_url.path
        supabase_host = supabase_host.strip().rstrip("/")

        if not AWS_S3_ENDPOINT_URL:
            AWS_S3_ENDPOINT_URL = f"https://{supabase_s3_host(supabase_host)}/storage/v1/s3"
        if not AWS_S3_CUSTOM_DOMAIN:
            AWS_S3_CUSTOM_DOMAIN = (
                f"{supabase_public_host(supabase_host)}/storage/v1/object/public/{AWS_STORAGE_BUCKET_NAME}"
            )

    if not AWS_S3_ENDPOINT_URL:
        raise ImproperlyConfigured(
            "AWS_S3_ENDPOINT_URL or SUPABASE_STORAGE_URL must be set when DJANGO_MEDIA_STORAGE=s3."
        )

    if not AWS_S3_CUSTOM_DOMAIN and "supabase.co/storage/v1/s3" in AWS_S3_ENDPOINT_URL:
        parsed_s3_endpoint = urlparse(AWS_S3_ENDPOINT_URL)
        public_host = supabase_public_host(parsed_s3_endpoint.netloc)
        AWS_S3_CUSTOM_DOMAIN = (
            f"{public_host}/storage/v1/object/public/{AWS_STORAGE_BUCKET_NAME}"
        )

    if AWS_S3_CUSTOM_DOMAIN.startswith("http://"):
        AWS_S3_CUSTOM_DOMAIN = AWS_S3_CUSTOM_DOMAIN.removeprefix("http://")
    elif AWS_S3_CUSTOM_DOMAIN.startswith("https://"):
        AWS_S3_CUSTOM_DOMAIN = AWS_S3_CUSTOM_DOMAIN.removeprefix("https://")

    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "access_key": AWS_ACCESS_KEY_ID,
            "secret_key": AWS_SECRET_ACCESS_KEY,
            "endpoint_url": AWS_S3_ENDPOINT_URL,
            "region_name": AWS_S3_REGION_NAME,
            # Supabase S3 requires path-style addressing:
            # /storage/v1/s3/<bucket>/<key>, not <bucket>.<host>/<key>.
            "addressing_style": AWS_S3_ADDRESSING_STYLE,
            "signature_version": AWS_S3_SIGNATURE_VERSION,
            "location": AWS_LOCATION,
            "file_overwrite": AWS_S3_FILE_OVERWRITE,
            # Supabase Storage public buckets do not need object ACL headers.
            # Keeping this as None avoids ACL-related upload failures.
            "default_acl": AWS_DEFAULT_ACL,
            "querystring_auth": AWS_QUERYSTRING_AUTH,
            "object_parameters": AWS_S3_OBJECT_PARAMETERS,
            "custom_domain": AWS_S3_CUSTOM_DOMAIN or None,
            "url_protocol": "https:",
        },
    }

    media_location = f"{AWS_LOCATION}/" if AWS_LOCATION else ""
    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/{media_location}"
    else:
        MEDIA_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/{media_location}"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CRONOMETRAGEM_API_KEY = os.getenv("CRONOMETRAGEM_API_KEY", "").strip()
CRONOMETRAGEM_RECEIVER_URL = os.getenv("CRONOMETRAGEM_RECEIVER_URL", "").strip()
CRONOMETRAGEM_RECEIVER_API_KEY = os.getenv("CRONOMETRAGEM_RECEIVER_API_KEY", "").strip()
CRONOMETRAGEM_RECEIVER_TIMEOUT = env_int("CRONOMETRAGEM_RECEIVER_TIMEOUT", 3)


REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
}


ADMIN_URL = os.getenv("DJANGO_ADMIN_URL", "admin/" if DEBUG else "")
if ADMIN_URL and not ADMIN_URL.endswith("/"):
    ADMIN_URL = f"{ADMIN_URL}/"
if not DEBUG and not ADMIN_URL:
    raise ImproperlyConfigured("DJANGO_ADMIN_URL must be set in production.")

LOGIN_URL = "auth_page"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", True)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "same-origin"

DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", 10 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", 5 * 1024 * 1024)


LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
