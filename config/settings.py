"""
Django settings for config project.
"""

from pathlib import Path
import environ
import os


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
ALLOWED_HOSTS=["*"]

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
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "suaplus"),
        "USER": os.environ.get("DB_USER", "suaplus_dev"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "suaplusdevelopment"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
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
#ACCOUNT_ALLOW_REGISTRATION = False
LOGIN_REDIRECT_URL = "/hub/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "/accounts/login/"

# Email OTP (AUTH-02)
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_REQUIRED = False
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 600
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 5

# Google OAuth Credentials (UITS Placeholders)
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_CLIENT_ID", default="YOUR_KNUST_CLIENT_ID"),
            "secret": env("GOOGLE_CLIENT_SECRET", default="YOUR_KNUST_CLIENT_SECRET"),
            "key": ""
        },
        "SCOPE": [
            "profile",
            "email",
        ],
        "AUTH_PARAMS": {
            "access_type": "online",
            "hd": "knust.edu.gh", 
        }
    }
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