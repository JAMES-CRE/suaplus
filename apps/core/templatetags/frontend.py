"""Presentation-only copy helpers; stored records remain untouched."""
from django import template
register = template.Library()

@register.filter
def frontend_text(value):
    if value is None:
        return ''
    return str(value).replace(chr(0x2014), ', ').replace(chr(0x2013), ' to ')
