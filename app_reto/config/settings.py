"""
Configuración del proyecto de monitoreo de cuartos fríos.

Sigue la misma estructura que el proyecto de la báscula (bascula V30-3):
la carpeta config/ tiene la configuración general y la lógica vive en una
app aparte (monitoreo/), para poder copiarla a otro proyecto si se necesita.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# La clave secreta la guardo en un archivo local para que no quede fija en el codigo
_SECRET_KEY_FILE = BASE_DIR / '.django_secret_key'
if _SECRET_KEY_FILE.exists():
    SECRET_KEY = _SECRET_KEY_FILE.read_text().strip()
else:
    from django.core.management.utils import get_random_secret_key
    SECRET_KEY = get_random_secret_key()
    _SECRET_KEY_FILE.write_text(SECRET_KEY)

DEBUG = True

ALLOWED_HOSTS = ['*']


# Aplicaciones instaladas

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'monitoreo',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Base de datos SQLite con modo WAL para permitir lecturas mientras el lector escribe.
# transaction_mode IMMEDIATE: si dos partes quieren escribir a la vez (el lector y la API),
# la segunda espera su turno (hasta `timeout` segundos) en lugar de fallar con "database is locked".
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-64000; PRAGMA temp_store=MEMORY;',
            'transaction_mode': 'IMMEDIATE',
            'timeout': 20,
        },
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Idioma y zona horaria. En la base de datos las fechas se guardan en UTC
# y la API las entrega convertidas a la hora local.
LANGUAGE_CODE = 'es-mx'

TIME_ZONE = 'America/Mexico_City'

USE_I18N = True

USE_TZ = True


STATIC_URL = 'static/'

STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Para entrar a /admin/ desde el navegador
LOGIN_URL = '/admin/login/'

# La interfaz tiene un botón "Registrar". Las cuentas creadas así no son administradoras.
# Ponerlo en False para que solo el administrador cree cuentas desde /admin/.
PERMITIR_REGISTRO = True


# Mensajes del lector en la consola
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[{name}] {levelname}: {message}', 'style': '{'},
    },
    'handlers': {
        'consola': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'loggers': {
        'monitoreo': {'handlers': ['consola'], 'level': 'INFO'},
    },
}
