from pathlib import Path

root = Path(__file__).resolve().parent

def read(name):
    return (root / name).read_text(encoding='utf-8')

def write(name, content):
    (root / name).write_text(content, encoding='utf-8')

landing = read('templates/public/index.html')
gallery = landing[landing.index('<section class="py-10 px-4'):landing.index('<!-- Divider -->', landing.index('<section class="py-10 px-4'))]
write('templates/public/index.html', "{% extends 'base.html' %}\n{% load static %}\n\n{% block content %}\n{% include 'public/_welcome.html' %}\n\n" + gallery.replace('py-10', 'py-8') + '{% endblock %}\n')

hub = read('templates/hub/home.html')
# Routine destinations already live in the shared navigation.
for start, end in [
    ('<!-- MY PASSPORT', '<!-- DASHBOARD'),
    ('<!-- DASHBOARD', '<!-- SCOUT LIST'),
    ('<!-- SESSIONS', '<!-- EXECUTIVE MEMOS'),
    ('<!-- COMPETENCY LIBRARY', '<!-- DATA REQUESTS'),
    ('<!-- REVIEW CREDENTIALS', '<!-- ACADEMIC CALENDAR'),
]:
    a, b = hub.index(start), hub.index(end)
    hub = hub[:a] + hub[b:]
hub = hub.replace('mb-8 md:mb-12', 'mb-6')
hub = hub.replace('<h2 class="text-xl font-semibold mb-2">All your tools</h2><p class="text-sm text-kec-grey mb-5">Tools available for your role, whenever you need them.</p>', '<h2 class="text-xl font-semibold mb-4">More tools</h2>')
write('templates/hub/home.html', hub)

write('templates/hub/_tasks.html', '''{% if next_session or pending_reviews %}
<section aria-labelledby="today-heading" class="mb-8">
    <h2 id="today-heading" class="text-xl font-semibold mb-4">Coming up</h2>
    <div class="grid grid-cols-1 {% if next_session and pending_reviews %}md:grid-cols-2{% endif %} gap-4">
        {% if can_sessions and next_session %}
        <div class="task-card flex flex-col items-start">
            <p class="text-sm font-medium text-kec-grey mb-2">Next workshop</p>
            <h3 class="task-card-title break-words">{{ next_session.title }}</h3>
            <p class="text-sm text-kec-grey">{{ next_session.start_at|date:"D d M Y, H:i" }} · {{ next_session.programme.name }}</p>
            {% if next_session.venue %}<p class="text-sm text-kec-grey mt-1">{{ next_session.venue }}</p>{% endif %}
            <a href="{% url 'session_console' next_session.id %}" class="btn-secondary btn-sm mt-5">Manage workshop</a>
        </div>
        {% endif %}
        {% if sua_nav.reviews and pending_reviews %}
        <div class="task-card border-amber-200 bg-amber-50 flex flex-col items-start">
            <h3 class="task-card-title">{{ pending_reviews }} credential{{ pending_reviews|pluralize }} awaiting review</h3>
            <p class="text-sm text-kec-grey">From your assigned programmes and units.</p>
            <a href="{% url 'credential_review_queue' %}" class="btn-secondary btn-sm mt-5">Open review queue</a>
        </div>
        {% endif %}
    </div>
</section>
{% endif %}
''')

passport = read('templates/passport/passport.html')
a, b = passport.index('    <!-- Profile Header -->'), passport.index('    <!-- Main Grid -->')
passport = passport[:a] + passport[b:]
passport = passport.replace('            <a href="{% url \'passport_profile\' token %}" class="btn-secondary text-center w-full sm:w-auto">My Profile</a>\n', '')
write('templates/passport/passport.html', passport)

write('templates/passport/_overview.html', '''<section class="mb-6" aria-labelledby="passport-summary-heading">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-5">
        <div class="flex items-start gap-3 min-w-0">
            <span class="w-12 h-12 shrink-0 rounded-full bg-kec-red flex items-center justify-center text-white text-lg font-bold" aria-hidden="true">{{ person.initials }}</span>
            <div class="min-w-0">
                <p class="text-sm text-kec-grey mb-1">Sua+ passport</p>
                <h1 id="passport-summary-heading" class="text-2xl sm:text-3xl font-semibold tracking-tight break-words">{% if person.title %}{{ person.title }} {% endif %}{{ person.full_name }}</h1>
                <p class="text-sm text-kec-grey mt-1 break-words">{{ person.unit.name|default:"Unassigned Unit" }} · {{ person.organisation.name|default:"KNUST" }}</p>
                <div class="flex flex-wrap items-center gap-2 text-sm mt-2">
                    <span class="text-kec-grey break-all">{{ masked_email }}</span>
                    <span class="px-2 py-0.5 rounded-full text-xs font-semibold {% if person.affiliation == 'external' %}bg-orange-100 text-orange-700{% else %}bg-green-100 text-green-700{% endif %}">{% if person.affiliation == 'external' %}External{% else %}Verified {{ person.get_affiliation_display }}{% endif %}</span>
                    <span class="text-kec-grey">{{ person.get_category_display }}</span>
                </div>
            </div>
        </div>
        <a href="{% url 'passport_profile' token %}" class="btn-secondary btn-sm shrink-0">View my profile</a>
    </div>
    <div class="border-l-4 border-kec-red bg-white rounded-r-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div class="min-w-0"><h2 class="text-base font-semibold">{{ next_action.title }}</h2><p class="text-sm text-kec-grey mt-1 break-words">{{ next_action.description }}</p></div>
        <a href="{{ next_action.url }}" class="btn-primary btn-sm shrink-0">{{ next_action.label }}</a>
    </div>
</section>
''')
print('Removed repeated sections and actions from the landing page, Hub, and passport.')
