import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / '.venv' / 'Lib' / 'site-packages'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
from django.conf import settings

settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
django.setup()

from django.core.management import call_command
from django.template.loader import get_template

count = 0
for path in (root / 'templates').rglob('*.html'):
    get_template(path.relative_to(root / 'templates').as_posix())
    count += 1
print(f'Compiled {count} templates.')
call_command('check')

import pytest

sys.exit(pytest.main(['apps/accounts/tests/test_ui_navigation.py', 'apps/passport/tests/test_passport_ui.py', '-q', '-p', 'no:cacheprovider']))
