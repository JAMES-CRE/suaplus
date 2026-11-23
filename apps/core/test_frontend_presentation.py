from pathlib import Path
from django.template.loader import render_to_string
from apps.passport.forms import AssessmentProfileForm
from apps.registry.models import Person


def test_checkbox_uses_distinct_widget_and_explicit_optional_fields(db):
    person = Person.objects.create(full_name='Presentation learner', affiliation='staff')
    form = AssessmentProfileForm(person=person)
    assert form.fields['consent'].widget.attrs['class'] == 'form-checkbox'
    html = render_to_string('partials/_field.html', {'field': form['consent']})
    assert 'checkbox-field' in html
    assert 'form-input' not in html
    assert 'Optional' in render_to_string('partials/_field.html', {'field': form['phone']})


def test_static_frontend_copy_contains_no_em_dash():
    root = Path(__file__).resolve().parents[2]
    for path in (root / 'templates').glob('**/*.html'):
        text = path.read_text(encoding='utf-8-sig')
        assert chr(0x2014) not in text, str(path)
        assert '&mdash;' not in text, str(path)


def test_form_controls_keep_accessible_error_summary(db):
    person = Person.objects.create(full_name='Presentation learner', affiliation='staff')
    form = AssessmentProfileForm({}, person=person)
    assert not form.is_valid()
    html = render_to_string('partials/_form_errors.html', {'form': form})
    assert 'data-error-summary' in html
    assert 'role="alert"' in html
    assert 'href="#id_full_name"' in html
