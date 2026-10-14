"""Safe formatting for RayNoor product descriptions, without accepting HTML."""
import re
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


def _inline(text):
    # Escape everything BEFORE emitting the markup we control. Untrusted HTML
    # never enters the DOM as tags or attributes.
    value = str(escape(text))
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", value)
    return value


@register.filter(name='rich_product_description')
def rich_product_description(value):
    """Render a deliberately small markup subset: strong/em, headings, lists, paragraphs."""
    source = str(value or '').replace('\r\n','\n').replace('\r','\n')[:12000]
    parts, block, list_kind, list_items = [], [], None, []

    def flush_paragraph():
        if block:
            parts.append('<p>' + '<br>'.join(_inline(line) for line in block) + '</p>')
            block.clear()

    def flush_list():
        nonlocal list_kind, list_items
        if list_kind:
            parts.append('<' + list_kind + '>' + ''.join('<li>' + _inline(item) + '</li>' for item in list_items) + '</' + list_kind + '>')
            list_kind, list_items = None, []

    for line in source.split('\n'):
        stripped = line.strip()
        if not stripped:
            flush_paragraph(); flush_list(); continue
        heading = re.match(r'^(#{2,3})\s+(.+)$', stripped)
        bullet = re.match(r'^[-]\s+(.+)$', stripped)
        number = re.match(r'^\d{1,3}[.]\s+(.+)$', stripped)
        if heading:
            flush_paragraph(); flush_list()
            tag = 'h3' if len(heading.group(1)) == 3 else 'h2'
            parts.append(f'<{tag}>' + _inline(heading.group(2)) + f'</{tag}>')
        elif bullet or number:
            flush_paragraph()
            kind = 'ul' if bullet else 'ol'
            if list_kind and list_kind != kind: flush_list()
            list_kind = kind
            list_items.append((bullet or number).group(1))
        else:
            flush_list(); block.append(stripped)
    flush_paragraph(); flush_list()
    return mark_safe(''.join(parts))


@register.filter(name='plain_product_description')
def plain_product_description(value):
    source = str(value or '')
    source = re.sub(r'(?m)^\s*(?:#{2,3}\s+|-\s+|\d{1,3}\.\s+)', '', source)
    return re.sub(r'\*\*|\*', '', source).replace('\n', ' ').strip()
