"""
Django settings for config project.
"""

from pathlib import Path
import environ
import os
from django.core.exceptions import ImproperlyConfigured


# 1. Environment & Base Directory 
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-nmjyt=n)#=bd@f#jsvv!7ougn8ao(&j3@jytm9&t_5=*)z2ni)",
)
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1","testserver","10.42.88.75"])

if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a secure value when DJANGO_DEBUG is false."
    )

if not DEBUG and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must list explicit hosts when DJANGO_DEBUG is false."
    )

# 2. Email, SMS & API Keys 
if env("SMTP_HOST", default=""):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("SMTP_HOST")
    EMAIL_PORT = env.int("SMTP_PORT", default=587)
    EMAIL_USE_TLS = env.bool("SMTP_USE_TLS", default=True)
    EMAIL_HOST_USER = env("SMTP_USER")
    EMAIL_HOST_PASSWORD = env("SMTP_PASSWORD")
    DEFAULT_FROM_EMAIL = env("SMTP_FROM_EMAIL", default="Sua+ <noreply@knust.edu.gh>")
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    DEFAULT_FROM_EMAIL = "Sua+ <noreply@suaplus.knust.edu.gh>"

SITE_URL = env("SITE_URL", default="http://127.0.0.1:8000")

# Gemini API (for memo generation)
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")

# SMS Provider Placeholder
SMS_PROVIDER_URL = env("SMS_PROVIDER_URL", default="")
SMS_PROVIDER_KEY = env("SMS_PROVIDER_KEY", default="")


# 3. Application Definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "django_otp",
    "django_otp.plugins.otp_totp",

    # Sua+ apps
    "apps.core",
    "apps.accounts",
    "apps.registry",
    "apps.library",
    "apps.programmes",
    "apps.sessions",
    "apps.passport",
    "apps.notifications",
    "apps.audit",
    "apps.reporting",
    "apps.imports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware", 
    "django_otp.middleware.OTPMiddleware",
    "apps.accounts.middleware.SuperAdminMFAMiddleware",
    "apps.accounts.idle_middleware.IdleTimeoutMiddleware",
    "apps.accounts.profile_middleware.ProfileCompletionMiddleware",
    "csp.middleware.CSPMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# 4. Database
if not DEBUG and not all(
    os.environ.get(name)
    for name in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")
):
    raise ImproperlyConfigured(
        "Production database settings DB_NAME, DB_USER, DB_PASSWORD, and DB_HOST must be configured."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="suaplus"),
        "USER": env("DB_USER", default="suaplus_dev" if DEBUG else ""),
        "PASSWORD": env("DB_PASSWORD", default="suaplusdevelopment" if DEBUG else ""),
        "HOST": env("DB_HOST", default="localhost" if DEBUG else ""),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}


# 5. Authentication & AllAuth
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

SITE_ID = 1

# AllAuth Core Settings
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"username", "email"}
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.KnustSocialAccountAdapter"
KNUST_ALLOWED_EMAIL_DOMAINS = tuple(
    domain.casefold().strip()
    for domain in env.list(
        "KNUST_ALLOWED_EMAIL_DOMAINS",
        default=["knust.edu.gh", "st.knust.edu.gh"],
    )
)
#ACCOUNT_ALLOW_REGISTRATION = False
LOGIN_REDIRECT_URL = "/hub/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"

# Email OTP (AUTH-02)
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_REQUIRED = False
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 600
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 5

# Enable Google sign-in only when UITS-provided OAuth credentials are present.
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="").strip()
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="").strip()
GOOGLE_OAUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
SOCIALACCOUNT_PROVIDERS = {}
if GOOGLE_OAUTH_ENABLED:
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APP": {
            "client_id": GOOGLE_CLIENT_ID,
            "secret": GOOGLE_CLIENT_SECRET,
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }


# 6. Internationalization & Static/Media
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# 7. Celery

CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Africa/Accra"

CELERY_BEAT_SCHEDULE = {
    "run-tier-engine-nightly": {
        "task": "apps.programmes.tasks.run_tier_engine_task",
        "schedule": 60.0 * 60.0 * 24.0,
    },
    "send-followup-surveys-daily": {
        "task": "apps.sessions.tasks.send_90_day_surveys",
        "schedule": 60.0 * 60.0 * 24.0,
    },
    "expire-passport-records-daily": {
        "task": "apps.passport.tasks.expire_passport_records_task",
        "schedule": 60.0 * 60.0 * 24.0,
    },
}


# 8. Cache & Security 

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "suaplus",
    }
}

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'", "https://cdn.jsdelivr.net", "'unsafe-inline'"],
        "style-src": ["'self'", "https://fonts.googleapis.com", "'unsafe-inline'"],
        "img-src": ["'self'", "data:", "https://api.qrserver.com"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
        "connect-src": ["'self'"],
    }
}

# Production deployments terminate TLS at the UITS reverse proxy. Keep these
# defaults enabled outside local development, with an environment override for
# staging setups that do not yet have HTTPS configured.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SESSION_COOKIE_SECURE = env.bool("DJANGO_SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("DJANGO_CSRF_COOKIE_SECURE", default=not DEBUG)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
    )
