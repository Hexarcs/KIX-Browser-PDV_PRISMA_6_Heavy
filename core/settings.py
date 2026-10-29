"""Configuração do browser-POS.

Aplicação mínima de terminal de pagamentos Lightning. As credenciais do
LNbits permanecem exclusivamente no servidor, carregadas do ambiente/.env.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def env_int(name, default, minimum=None):
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    return value


def env_float(name, default, minimum=None):
    raw = (os.getenv(name) or "").strip()
    try:
        value = float(raw) if raw else float(default)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    return value


SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY não definida. Crie o .env a partir de .env.example."
    )

DEBUG = env_bool("DEBUG", False)

ALLOWED_HOSTS = [
    host.strip()
    for host in (os.getenv("ALLOWED_HOSTS") or "").split(",")
    if host.strip()
] or ["127.0.0.1", "localhost"]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in (os.getenv("CSRF_TRUSTED_ORIGINS") or "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "core",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"


def database_path():
    """Mantém o banco existente e aceita um caminho explícito por ambiente.

    O repositório atual guarda o SQLite em ``db.sqlite3``. Instalações antigas
    em Docker podem ter ``data/db.sqlite3``; nesse caso ele continua sendo
    usado. Nenhum arquivo é movido ou apagado.
    """
    configured = (os.getenv("DATABASE_PATH") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    legacy_volume = BASE_DIR / "data" / "db.sqlite3"
    if legacy_volume.exists():
        return legacy_volume
    return BASE_DIR / "db.sqlite3"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": database_path(),
        "OPTIONS": {"timeout": 20},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
# Os arquivos ficam em core/static e são encontrados pelo AppDirectoriesFinder.
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# LNbits / Lightning
# ---------------------------------------------------------------------------
LNBITS_URL = (os.getenv("LNBITS_URL") or "").strip().rstrip("/")
LNBITS_API_KEY = (os.getenv("LNBITS_API_KEY") or "").strip()
LNBITS_TIMEOUT = env_float("LNBITS_TIMEOUT", 10, minimum=1)

# Limites locais. O LNbits pode impor limites menores; nesse caso a criação da
# invoice é recusada pelo próprio serviço e a API devolve erro genérico.
POS_MIN_SATS = env_int("POS_MIN_SATS", 1, minimum=1)
POS_MAX_SATS = env_int("POS_MAX_SATS", 1_000_000, minimum=POS_MIN_SATS)
POS_INVOICE_TTL_SECONDS = env_int("POS_INVOICE_TTL_SECONDS", 3600, minimum=60)

# ---------------------------------------------------------------------------
# Sessão, CSRF e cabeçalhos de segurança
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True

# Em produção HTTPS, mantenha os padrões (Secure). Para desenvolvimento HTTP
# com DEBUG=False, defina explicitamente as variáveis como false.
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "no-referrer"
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0, minimum=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = (
    env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    if SECURE_HSTS_SECONDS
    else False
)
SECURE_HSTS_PRELOAD = (
    env_bool("SECURE_HSTS_PRELOAD", False) if SECURE_HSTS_SECONDS else False
)

if env_bool("TRUST_PROXY_HEADERS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# ---------------------------------------------------------------------------
# Logs (sem corpo de requisição, invoices ou credenciais)
# ---------------------------------------------------------------------------
LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "core": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
