import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as Obj

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / '.venv' / 'Lib' / 'site-packages'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django

django.setup()
from django.template.loader import get_template

person = Obj(full_name='Demo learner', initials='DL', title='', unit=Obj(name='KNUST'), organisation=Obj(name='E-Learning Centre'), affiliation='staff', get_affiliation_display='Staff', get_category_display='Academic')
context = {
    'person': person, 'user': Obj(is_authenticated=True, email='demo@example.com'),
    'request': Obj(path='/hub/', resolver_match=Obj(url_name='hub_home')),
    'sua_nav': dict.fromkeys(['sessions', 'reviews', 'reports', 'library'], True),
    'is_admin': True, 'is_owner': True, 'is_exec': True, 'can_sessions': True,
    'pending_reviews': 2, 'next_session': Obj(id=1, title='Digital teaching workshop', start_at=datetime(2026, 11, 18, 10, tzinfo=UTC), programme=Obj(name='Digital pedagogy'), venue='KNUST E-Learning Centre'),
    'csrf_token': 'preview', 'token': 'preview-token', 'masked_email': 'd***@example.com',
    'next_action': {'title': 'Choose your next learning goal', 'description': 'Set a skill you want to develop and give yourself a target date.', 'url': '#learning-goals', 'label': 'Set a learning goal'},
}
for name in ['hub/home.html', 'passport/passport.html']:
    content = get_template(name).render(context)
    target = root / 'static' / ('.ui-preview-' + name.split('/')[0] + '.html')
    target.write_text(content, encoding='utf-8')
    print(f'Rendered {name} using demo data.')
