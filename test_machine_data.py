"""
Self-check for modal record loading (full history) and the post-lookup index.

Run:  ./venv/bin/python test_machine_data.py
Read-only against the database.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection, reset_queries  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from tracebility import models  # noqa: E402
from tracebility.views import (  # noqa: E402
    SSE_RECORD_LIMIT, _apply_limit, _index_posts_by, _match_post, get_machine_counts, get_machine_data,
)

PREP, POST = models.CiPreprocessing, models.CiPostprocessing


def test_apply_limit_none_returns_everything():
    total = PREP.objects.count()
    assert len(_apply_limit(PREP.objects.all(), None)) == total
    assert len(_apply_limit(PREP.objects.all(), 25)) == 25


def test_modal_gets_full_history_not_a_100_row_sample():
    total = PREP.objects.count()
    assert total > 100, 'need a table bigger than the SSE cap to prove anything'
    records = get_machine_data(PREP, POST, limit=None)
    assert len(records) == total, f'got {len(records)} of {total}'


def test_index_returns_same_post_as_a_per_record_query():
    index = _index_posts_by(POST)
    for prep in PREP.objects.all()[:300]:
        expected = POST.objects.filter(pre_id=prep.id).first() or POST.objects.filter(qr_data=prep.qr_data).first()
        actual = _match_post(index, prep)
        assert (expected.id if expected else None) == (actual.id if actual else None), f'mismatch for prep {prep.id}'


def test_full_history_does_not_issue_a_query_per_record():
    with CaptureQueriesContext(connection) as ctx:
        records = get_machine_data(PREP, POST, limit=None)
    assert len(records) > 1000
    assert len(ctx) <= 5, f'{len(ctx)} queries for {len(records)} records - N+1 is back'


def test_capped_caller_does_not_scan_the_whole_post_table():
    with CaptureQueriesContext(connection) as ctx:
        records = get_machine_data(PREP, POST, limit=SSE_RECORD_LIMIT)
    assert len(records) == SSE_RECORD_LIMIT
    assert len(ctx) <= 5, f'{len(ctx)} queries'
    sql = ' '.join(q['sql'] for q in ctx).upper()
    assert 'IN (' in sql, 'expected a restricted IN (...) lookup, not a full scan'


def test_limiting_changes_how_many_records_not_what_they_say():
    full = {r['prep_id']: r for r in get_machine_data(PREP, POST, limit=None)}
    capped = get_machine_data(PREP, POST, limit=50)
    assert len(capped) == 50
    for record in capped:
        reference = full[record['prep_id']]
        for field in ('qr_code', 'overall_status', 'post_id', 'post_status', 'prep_timestamp'):
            assert record[field] == reference[field], f'{field} differs for prep_id {record["prep_id"]}'


def test_records_are_sorted_pending_first_then_newest():
    keys = [(r['sort_priority'], -r['prep_id']) for r in get_machine_data(PREP, POST)]
    assert keys == sorted(keys)


def test_timestamps_are_iso_never_raw():
    for record in get_machine_data(PREP, POST, limit=200):
        assert 'T' in record['prep_timestamp'], record['prep_timestamp']
        assert record['post_timestamp'] is None or 'T' in record['post_timestamp']


def test_counts_sum_to_sample_and_use_few_queries():
    with CaptureQueriesContext(connection) as ctx:
        counts = get_machine_counts(PREP, POST)
    assert sum(counts.values()) == 100, counts
    assert len(ctx) <= 3, f'{len(ctx)} queries for counts'


if __name__ == '__main__':
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith('test_'):
            continue
        reset_queries()
        try:
            fn()
            print(f'ok  {name}')
        except AssertionError as exc:
            failures.append(name)
            print(f'FAIL {name}: {exc}')
    print('\n' + ('All machine-data checks passed.' if not failures else f'{len(failures)} FAILED'))
    raise SystemExit(1 if failures else 0)
