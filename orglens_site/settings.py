"""
Settings for OrgLens.

Trimmed down on purpose. OrgLens does no persistence and has no accounts,
so the database, auth, admin, sessions and messages apps are all removed
rather than left switched on and unused.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Fine to hardcode: this is a local review exercise, not a deployed service.
SECRET_KEY = "django-insecure-orglens-local-only-not-for-deployment"

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "preview",
]

# CSRF stays because the upload form posts. Session and auth middleware are
# gone because nothing in the app reads request.user or request.session.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "orglens_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "orglens_site.wsgi.application"

# No DATABASES entry at all. Uploads are analyzed in memory and thrown away.

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
