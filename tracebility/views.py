"""
Dashboard views - Imperial plant.

Django is read-only here: Node-RED writes the tables, this module reads them.
Every machine in this plant is the "standard" family: a preprocessing table
(part loaded) and a postprocessing table (part judged OK/NG), linked by
post.pre_id -> prep.id, with qr_data as a fallback link.
"""
import csv
import json
import time
from datetime import datetime, timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone

from . import models

# ============================================================================
# MACHINE CONFIGURATION
# ============================================================================

# ponytail: IPs and op codes are not in the database dump - placeholders,
# edit here once the plant confirms them.
MACHINE_CONFIGS = [
    {'name': 'Camera Inspection(OP-10)', 'display_name': 'Camera Inspection', 'ip': '192.168.1.201', 'op_code': 'OP-10',
     'prep_model': models.CiPreprocessing, 'post_model': models.CiPostprocessing, 'type': 'inspection'},
    {'name': 'Autofatash(OP-20)', 'display_name': 'Autofatash', 'ip': '192.168.1.202', 'op_code': 'OP-20',
     'prep_model': models.AutoPreprocessing, 'post_model': models.AutoPostprocessing, 'type': 'auto'},
    {'name': 'Helium Station(OP-30)', 'display_name': 'Helium Station', 'ip': '192.168.1.203', 'op_code': 'OP-30',
     'prep_model': models.HeliumPreprocessing, 'post_model': models.HeliumPostprocessing, 'type': 'helium'},
]

# No assembly / one-off machines in this plant. Kept so the shape matches the
# reference app and monitoring can iterate one list.
ASSEMBLY_CONFIGS = []
ALL_CONFIGS = MACHINE_CONFIGS + ASSEMBLY_CONFIGS

# The modal loads full history (limit=None). The SSE stream re-sends its whole
# payload every time the row count changes, so it stays capped and the browser
# merges new rows into the list it already has.
SSE_RECORD_LIMIT = 100

# Above this many rows, index the whole postprocessing table rather than
# building a huge IN (...) clause.
POST_INDEX_RESTRICT_MAX = 2000

# Counts sample the newest rows: "recent quality", not lifetime totals.
COUNTS_SAMPLE_ROWS = 100

# A machine is ACTIVE if its newest prep row is younger than this.
ACTIVE_WINDOW = timedelta(minutes=21)

# When streaming rows newest-first, stop after this many consecutive rows that
# fall before the window start. Ids are chronological, so this is a safety
# margin against a handful of out-of-order inserts, not a data cap.
STOP_AFTER_OLDER_ROWS = 200

SSE_POLL_SECONDS = 2


def machine_id(config):
    return config['name'].lower().replace(' ', '_').replace('(', '').replace(')', '').replace('-', '_')


def get_config(machine_name):
    return next((c for c in ALL_CONFIGS if machine_id(c) == machine_name), None)


def model_from_qr(qr):
    """Model code = the QR text before its first '#', e.g. 68603833AA#190#2026#0980 -> 68603833AA."""
    if not qr:
        return 'N/A'
    return qr.split('#', 1)[0] or 'N/A'


# ============================================================================
# TIMESTAMPS
# ============================================================================

# Node-RED writes 'YYYY-MM-DD HH:MM:SS' for this plant; it is tried first.
TIMESTAMP_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%d/%m/%Y, %I:%M:%S %p',
    '%d/%m/%Y %H:%M:%S',
    '%d/%m/%Y, %H:%M:%S',
    '%d/%m/%Y',
    '%m/%d/%Y, %I:%M:%S %p',
    '%d-%m-%Y %H:%M:%S',
    '%m-%d-%Y %H:%M:%S',
)


def parse_timestamp_to_datetime(timestamp):
    """Parse whatever Node-RED wrote into a naive datetime, or None."""
    if timestamp is None or timestamp == '':
        return None
    if hasattr(timestamp, 'strftime'):
        return timestamp
    if not isinstance(timestamp, str):
        return None
    text = timestamp.strip()
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None


def to_iso(timestamp):
    """Naive ISO-8601 for the browser ('' when missing/unparseable). Never send raw strings."""
    dt = parse_timestamp_to_datetime(timestamp)
    return dt.isoformat() if dt else ''


# ============================================================================
# QUERY HELPERS
# ============================================================================

def _apply_limit(queryset, limit):
    """Slice a queryset, or return it whole when limit is None."""
    return queryset if limit is None else queryset[:limit]


def _index_posts_by(post_model, restrict_to=None):
    """
    Build {'pre_id': {pre_id: post}, 'qr_data': {qr: post}} in ONE query.

    Iterating in the model's default ordering (-id) and keeping the first value
    seen reproduces what .filter(...).first() returned, minus the N+1.
    restrict_to = {'pre_id': {...}, 'qr_data': {...}} limits the scan to the
    rows a capped caller can actually match (IN (...) instead of a full scan).
    """
    index = {'pre_id': {}, 'qr_data': {}}
    if post_model is None:
        return index
    queryset = post_model.objects.all()
    if restrict_to is not None:
        matches = Q()
        for field, values in restrict_to.items():
            if values:
                matches |= Q(**{f'{field}__in': values})
        if not matches:
            return index
        queryset = queryset.filter(matches)
    for post in queryset.iterator(chunk_size=2000):
        for field in index:
            value = getattr(post, field, None)
            if value is not None and value not in index[field]:
                index[field][value] = post
    return index


def _restrict_for(preps):
    return {'pre_id': {p.id for p in preps}, 'qr_data': {p.qr_data for p in preps if p.qr_data}}


def _match_post(index, prep):
    """post.pre_id -> prep.id first, then qr_data."""
    return index['pre_id'].get(prep.id) or index['qr_data'].get(prep.qr_data)


def _stream_in_window(queryset, timestamp_of, start_dt, end_dt):
    """
    Yield (row, dt, timestamp_iso) for rows inside [start_dt, end_dt], newest-first.

    Timestamps are strings, so the window check runs in Python. Rows stream via
    .iterator(); ids are chronological, so after STOP_AFTER_OLDER_ROWS
    consecutive pre-window rows nothing older can match and we stop. There is
    deliberately no row cap: slicing before this filter dropped old rows.
    """
    older_streak = 0
    for row in queryset.iterator(chunk_size=2000):
        dt = parse_timestamp_to_datetime(timestamp_of(row))
        timestamp_iso = dt.isoformat() if dt else ''
        if (start_dt or end_dt) and dt:
            dt_cmp = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            if start_dt and dt_cmp < start_dt:
                older_streak += 1
                if older_streak >= STOP_AFTER_OLDER_ROWS:
                    return
                continue
            older_streak = 0
            if end_dt and dt_cmp > end_dt:
                continue
        yield row, dt, timestamp_iso


# ============================================================================
# RECORD BUILDING
# ============================================================================

def _post_status(post):
    if post is None:
        return 'Pending'
    return post.status if post.status in ('OK', 'NG') else 'Pending'


def build_record(prep, post):
    """One row of a machine's history: prep + matched post. Timestamps are ISO."""
    status = _post_status(post)
    record = {
        'prep_id': prep.id,
        'prep_timestamp': to_iso(prep.timestamp),
        'prep_machine_name': prep.machine_name,
        'model_name': model_from_qr(prep.qr_data),
        'previous_machine_status': prep.pervious_status or '-',
        'prep_status': 'OK',
        'qr_code': prep.qr_data,
        'post_id': post.id if post else None,
        'post_timestamp': to_iso(post.timestamp) if post else None,
        'post_status': status,
        'overall_status': status,
        'status_class': {'OK': 'completed-ok', 'NG': 'completed-ng'}.get(status, 'in-progress'),
        'sort_priority': 1 if post else 2,
        'batch_id': getattr(prep, 'batch_id', None) or (getattr(post, 'batch_id', None) if post else None),
        'batch_size': getattr(prep, 'batch_size', None),
        'slot_no': getattr(prep, 'slot_no', None),
    }
    return record


def get_machine_data(prep_model, post_model, machine_type='standard', limit=None):
    """All (or newest `limit`) prep rows with their matched post row. One post query total."""
    preps = list(_apply_limit(prep_model.objects.all(), limit))
    restrict_to = None if limit is None else _restrict_for(preps)
    index = _index_posts_by(post_model, restrict_to)
    records = [build_record(prep, _match_post(index, prep)) for prep in preps]
    records.sort(key=lambda r: (r['sort_priority'], -r['prep_id']))
    return records


def get_machine_counts(prep_model, post_model, machine_type='standard'):
    """OK/NG/Pending over the newest COUNTS_SAMPLE_ROWS prep rows."""
    counts = {'ok': 0, 'ng': 0, 'pending': 0}
    try:
        preps = list(prep_model.objects.all()[:COUNTS_SAMPLE_ROWS])
        index = _index_posts_by(post_model, _restrict_for(preps))
        for prep in preps:
            counts[_post_status(_match_post(index, prep)).lower()] += 1
    except Exception as exc:  # DB hiccup must not take the dashboard down
        print(f'Error getting counts: {exc}')
    return counts


def get_latest_machine_record(prep_model, post_model, machine_type='standard'):
    latest = prep_model.objects.first()
    if not latest:
        return None
    index = _index_posts_by(post_model, _restrict_for([latest]))
    record = build_record(latest, _match_post(index, latest))
    record['has_post'] = record['post_id'] is not None
    return record


def check_machine_status(prep_model):
    """ACTIVE = newest prep row is within ACTIVE_WINDOW of now."""
    try:
        latest = prep_model.objects.first()
        if not latest:
            return False
        dt = parse_timestamp_to_datetime(latest.timestamp)
        if not dt:
            return False
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt >= timezone.now() - ACTIVE_WINDOW
    except Exception as exc:
        print(f'Error checking machine status: {exc}')
        return False


def machine_summary(config):
    """Everything a dashboard card needs. Feeds both the page render and the SSE stream."""
    prep, post = config['prep_model'], config['post_model']
    return {
        'name': config['name'],
        'display_name': config['display_name'],
        'ip': config['ip'],
        'op_code': config['op_code'],
        'type': config['type'],
        'machine_id': machine_id(config),
        'is_active': check_machine_status(prep),
        'is_assembly': False,
        'latest_record': get_latest_machine_record(prep, post),
        'counts': get_machine_counts(prep, post),
    }


def get_dashboard_summary():
    return [machine_summary(c) for c in ALL_CONFIGS]


def machine_payload(config, limit):
    records = get_machine_data(config['prep_model'], config['post_model'], limit=limit)
    return {
        'machine_name': config['name'],
        'display_name': config['display_name'],
        'ip': config['ip'],
        'op_code': config['op_code'],
        'machine_type': 'standard',
        'is_active': check_machine_status(config['prep_model']),
        'records': records,
        'total': len(records),
        'is_assembly': False,
        'counts': get_machine_counts(config['prep_model'], config['post_model']),
    }


# ============================================================================
# VIEWS
# ============================================================================

def dashboard_view(request):
    return render(request, 'dashboard/dashboard.html', {'machines': get_dashboard_summary()})


def machine_detail_view(request, machine_name):
    config = get_config(machine_name)
    if not config:
        return render(request, 'dashboard/machine_detail.html', {'error': 'Machine not found'})
    records = get_machine_data(config['prep_model'], config['post_model'])
    return render(request, 'dashboard/machine_detail.html', {
        'machine_name': config['name'],
        'machine_id': machine_name,
        'display_name': config['display_name'],
        'ip': config['ip'],
        'op_code': config['op_code'],
        'machine_type': 'standard',
        'is_active': check_machine_status(config['prep_model']),
        'records': records,
        'records_json': json.dumps(records, cls=DjangoJSONEncoder),
        'is_assembly': False,
    })


def machine_data_api(request, machine_name):
    """Modal data: full history by default, ?limit=N as an escape hatch."""
    config = get_config(machine_name)
    if not config:
        return JsonResponse({'error': 'Machine not found'}, status=404)
    try:
        limit = int(request.GET['limit'])
        limit = limit if limit > 0 else None
    except (KeyError, ValueError):
        limit = None
    return JsonResponse(machine_payload(config, limit))


def search_qr_code(request):
    """One QR's journey across all machines."""
    qr_code = request.GET.get('qr', '').strip()
    if not qr_code:
        return JsonResponse({'error': 'No QR code provided'}, status=400)
    results = []
    for config in ALL_CONFIGS:
        prep_count = config['prep_model'].objects.filter(qr_data__icontains=qr_code).count()
        post_count = config['post_model'].objects.filter(qr_data__icontains=qr_code).count()
        if prep_count or post_count:
            results.append({
                'machine': config['name'],
                'display_name': config['display_name'],
                'preprocessing_count': prep_count,
                'postprocessing_count': post_count,
            })
    return JsonResponse({'qr_code': qr_code, 'results': results})


CSV_COLUMNS = [
    ('ID', 'prep_id'), ('QR Code', 'qr_code'), ('Model', 'model_name'),
    ('Prep Timestamp', 'prep_timestamp'), ('Post ID', 'post_id'), ('Post Timestamp', 'post_timestamp'),
    ('Status', 'post_status'), ('Overall Status', 'overall_status'),
    ('Previous Machine Status', 'previous_machine_status'),
    ('Batch ID', 'batch_id'), ('Batch Size', 'batch_size'), ('Slot No', 'slot_no'),
]


def export_machine_data(request, machine_name):
    """Full history of one machine as CSV."""
    config = get_config(machine_name)
    if not config:
        return HttpResponse('Machine not found', status=404)
    records = get_machine_data(config['prep_model'], config['post_model'])
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{machine_name}_data.csv"'
    writer = csv.writer(response)
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for record in records:
        writer.writerow([record.get(key) if record.get(key) is not None else '-' for _, key in CSV_COLUMNS])
    return response


# ============================================================================
# SERVER-SENT EVENTS
# ============================================================================

def _row_counts(configs):
    return {
        m._meta.db_table: m.objects.count()
        for c in configs for m in (c['prep_model'], c['post_model']) if m
    }


def _sse_response(generator):
    response = StreamingHttpResponse(generator, content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def _sse_loop(configs, build_payload):
    """Poll row counts every SSE_POLL_SECONDS; push build_payload() when any changes."""
    last = _row_counts(configs)
    while True:
        try:
            current = _row_counts(configs)
            if current != last:
                last = current
                yield f'data: {json.dumps(build_payload(), cls=DjangoJSONEncoder)}\n\n'
            yield ': heartbeat\n\n'
            time.sleep(SSE_POLL_SECONDS)
        except GeneratorExit:
            break
        except Exception as exc:
            print(f'SSE Error: {exc}')
            yield f'data: {json.dumps({"error": str(exc)})}\n\n'
            time.sleep(5)


def sse_dashboard_stream(request):
    return _sse_response(_sse_loop(
        ALL_CONFIGS, lambda: {'type': 'update', 'machines': get_dashboard_summary()},
    ))


def sse_machine_stream(request, machine_name):
    config = get_config(machine_name)
    if not config:
        return StreamingHttpResponse('Machine not found', status=404)
    return _sse_response(_sse_loop(
        [config], lambda: {'type': 'update', **machine_payload(config, SSE_RECORD_LIMIT)},
    ))
