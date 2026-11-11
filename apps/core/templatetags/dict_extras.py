"""Custom template filters for dictionary access."""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Return dictionary[key] or empty string."""
    if dictionary is None:
        return ""
    try:
        return dictionary.get(key, "")
    except AttributeError:
        return ""