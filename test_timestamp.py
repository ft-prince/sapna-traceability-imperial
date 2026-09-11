"""
Self-check for timestamp parsing / normalisation.

Run:  ./venv/bin/python test_timestamp.py
No database needed - parse_timestamp_to_datetime and to_iso are pure.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from datetime import datetime  # noqa: E402

from tracebility.views import parse_timestamp_to_datetime, to_iso  # noqa: E402


def test_plant_format_is_first_and_exact():
    # What Node-RED writes for Imperial (verified on real rows).
    assert to_iso('2026-09-09 22:06:38') == '2026-09-09T22:06:38'
    assert to_iso('2026-09-09 22:06:38.123456') == '2026-09-09T22:06:38.123456'


def test_other_formats_the_reference_parser_knew():
    assert to_iso('04/06/2026, 09:08:37 AM') == '2026-06-04T09:08:37'
    assert to_iso('10/8/2026, 2:36:10 pm') == '2026-08-10T14:36:10'
    assert to_iso('04/06/2026 09:08:37') == '2026-06-04T09:08:37'
    assert to_iso('25-08-2026 09:08:37') == '2026-08-25T09:08:37'
    assert to_iso('2026-06-04T09:08:37') == '2026-06-04T09:08:37'
    assert to_iso(datetime(2026, 6, 4, 9, 8, 37)) == '2026-06-04T09:08:37'


def test_day_is_not_silently_read_as_month():
    # 04/06 is 4 June, NOT 6 April (JS new Date() gets this wrong; we must not).
    assert to_iso('04/06/2026, 09:08:37 AM').startswith('2026-06-04')
    # day > 12 must parse, not fail.
    assert to_iso('25/08/2026 10:00:00') == '2026-08-25T10:00:00'


def test_unparseable_returns_empty_not_garbage():
    assert to_iso(None) == ''
    assert to_iso('') == ''
    assert to_iso('not a timestamp') == ''
    assert parse_timestamp_to_datetime('31/31/2026 00:00:00') is None


def test_iso_output_sorts_chronologically():
    stamps = [to_iso('2026-08-25 10:00:00'), to_iso('2026-09-09 22:06:38')]
    assert sorted(stamps) == stamps


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
            print(f'ok  {name}')
    print('\nAll timestamp checks passed.')
