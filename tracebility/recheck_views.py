"""
Recheck page - the only place in this app that writes to the machine tables.

An operator finds a failed record, changes its status, and the change is
stamped with who did it and when. Everything else Django does here is
read-only; Node-RED remains the only writer of production rows.

Access control: all four endpoints are @login_required, search results are
filtered to the operator's assigned stations, and the update / record-detail
endpoints return 403 for a station the operator is not assigned to. Filtering
the dropdown alone would be decoration, since the station id arrives as a
parameter.
"""
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .permissions import (
    filter_machines_for_user, get_allowed_machine_ids, get_all_machines,
    is_machine_allowed, machine_id_for_name,
)
from .views import (
    ALL_CONFIGS, POST_INDEX_RESTRICT_MAX, _index_posts_by, _match_post, _post_status,
    _restrict_for, _stream_in_window, get_config, machine_id, model_from_qr, to_iso,
)

# Node-RED copies a machine's post status into the next machine's
# previous_machine_status, so downstream flows may compare against the literal
# 'REWORK'. The stored value stays REWORK; operators see the label RECHECK.
RECHECK_VALUE = 'REWORK'
RECHECK_LABEL = 'RECHECK'

# Statuses an operator may change. A part that passed, or that the machine has
# not judged yet, is not theirs to touch - only failures and parts already
# being reworked. One constant so the rule is changed in one place.
EDITABLE_STATUSES = ('NG', RECHECK_VALUE)

# What a record may be changed to.
ALLOWED_NEW_STATUSES = ('OK', 'NG', RECHECK_VALUE)

DEFAULT_WINDOW_HOURS = 24


def status_options():
    """[(stored value, label shown to the operator)] for the dropdowns."""
    return [('OK', 'OK'), ('NG', 'NG'), (RECHECK_VALUE, RECHECK_LABEL)]


def get_machine_list():
    """Stations for the dropdown. Same ids permissions assigns on."""
    return get_all_machines()


def get_all_model_names():
    """Distinct model codes across every prep table."""
    names = set()
    for config in ALL_CONFIGS:
        try:
            for qr in config['prep_model'].objects.values_list('qr_data', flat=True).distinct():
                names.add(model_from_qr(qr))
        except Exception as exc:
            print(f'Error listing models for {config["name"]}: {exc}')
    names.discard('N/A')
    return sorted(names)


def audit_of(record):
    """Who last changed this row and when, as JSON-safe values. None -> blanks."""
    if record is None:
        return {'last_updated_by': None, 'last_updated_at': None}
    stamp = getattr(record, 'last_updated_at', None)
    return {
        'last_updated_by': getattr(record, 'last_updated_by', None) or None,
        'last_updated_at': stamp.isoformat() if hasattr(stamp, 'isoformat') else None,
    }


def _window(filters):
    """(start, end) from explicit dates, else the last DEFAULT_WINDOW_HOURS."""
    start, end = filters.get('start_date', ''), filters.get('end_date', '')
    if start and end:
        try:
            return (
                timezone.make_aware(datetime.strptime(start, '%Y-%m-%d')),
                timezone.make_aware(
                    datetime.strptime(end, '%Y-%m-%d').replace(hour=23, minute=59, second=59)),
            )
        except ValueError:
            pass
    now = timezone.now()
    return now - timedelta(hours=DEFAULT_WINDOW_HOURS), now


def search_across_all_machines(filters):
    """
    Records matching the filters, newest first.

    Same two-phase shape as monitoring: stream prep rows through the date
    window without slicing first, then resolve their post rows with a single
    indexed query per machine.
    """
    qr_code = filters.get('qr_code', '').strip()
    model_name = filters.get('model_name', '')
    status_filter = filters.get('status', '')
    start_dt, end_dt = _window(filters)

    selected = filters.get('machine', '')
    if selected and selected != 'all':
        config = get_config(selected)
        configs = [config] if config else []
    else:
        configs = ALL_CONFIGS

    results = []
    for config in configs:
        query = Q()
        if qr_code:
            query &= Q(qr_data__icontains=qr_code)
        if model_name and model_name != 'all':
            query &= Q(qr_data__startswith=f'{model_name}#')

        try:
            matched = list(_stream_in_window(
                config['prep_model'].objects.filter(query), lambda p: p.timestamp, start_dt, end_dt,
            ))
        except Exception as exc:
            print(f'Error querying {config["name"]}: {exc}')
            continue

        restrict = _restrict_for([p for p, _, _ in matched]) if len(matched) <= POST_INDEX_RESTRICT_MAX else None
        index = _index_posts_by(config['post_model'], restrict)

        for prep, _dt, timestamp_iso in matched:
            post = _match_post(index, prep)
            status = _post_status(post)
            if status_filter and status_filter != 'all' and status != status_filter:
                continue

            results.append({
                'prep_id': prep.id,
                'post_id': post.id if post else None,
                'machine_id': machine_id(config),
                'machine_name': config['name'],
                'display_name': config['display_name'],
                'machine_type': config['type'],
                'qr_code': prep.qr_data,
                'model_name': model_from_qr(prep.qr_data),
                'timestamp': timestamp_iso,
                'post_timestamp': to_iso(post.timestamp) if post else '',
                'status': status,
                'status_label': RECHECK_LABEL if status == RECHECK_VALUE else status,
                'previous_machine_status': prep.pervious_status or '-',
                'editable': status in EDITABLE_STATUSES,
                **audit_of(post),
            })

    results.sort(key=lambda r: r['timestamp'], reverse=True)
    return results


@login_required
def recheck_page(request):
    return render(request, 'recheck/recheck.html', {
        'model_names': get_all_model_names(),
        'machines': filter_machines_for_user(request.user, get_machine_list()),
        'status_options': status_options(),
        'recheck_value': RECHECK_VALUE,
        'recheck_label': RECHECK_LABEL,
        'editable_statuses': ', '.join(
            RECHECK_LABEL if s == RECHECK_VALUE else s for s in EDITABLE_STATUSES),
    })


@csrf_exempt
@login_required
def recheck_search_api(request):
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)

    results = search_across_all_machines({
        key: request.GET.get(key, '')
        for key in ('qr_code', 'model_name', 'machine', 'start_date', 'end_date', 'status')
    })

    # The station id arrives as a parameter, so filter the results themselves.
    allowed = get_allowed_machine_ids(request.user)
    if allowed is not None:
        results = [r for r in results if r['machine_id'] in allowed]

    return JsonResponse({'success': True, 'count': len(results), 'results': results})


def _record_for(config, prep_id):
    """(prep, post) for one record, or (None, None) if the prep row is gone."""
    prep = config['prep_model'].objects.filter(id=prep_id).first()
    if prep is None:
        return None, None
    return prep, _match_post(_index_posts_by(config['post_model'], _restrict_for([prep])), prep)


@csrf_exempt
@login_required
def recheck_get_record_api(request, machine_name, prep_id):
    """Full detail for one record. machine_name is the station id from the URL."""
    if not is_machine_allowed(request.user, machine_name):
        return JsonResponse(
            {'success': False, 'error': 'You are not assigned to this station.'}, status=403)

    config = get_config(machine_name)
    if not config:
        return JsonResponse({'success': False, 'error': 'Machine not found'}, status=404)

    prep, post = _record_for(config, prep_id)
    if prep is None:
        return JsonResponse({'success': False, 'error': 'Record not found'}, status=404)

    status = _post_status(post)
    return JsonResponse({'success': True, 'record': {
        'prep_id': prep.id,
        'post_id': post.id if post else None,
        'machine_id': machine_name,
        'machine_name': config['name'],
        'display_name': config['display_name'],
        'machine_type': config['type'],
        'qr_code': prep.qr_data,
        'model_name': model_from_qr(prep.qr_data),
        'timestamp': to_iso(prep.timestamp),
        'post_timestamp': to_iso(post.timestamp) if post else '',
        'status': status,
        'status_label': RECHECK_LABEL if status == RECHECK_VALUE else status,
        'previous_machine_status': prep.pervious_status or '-',
        'machine_reported': prep.machine_name,
        'batch_id': getattr(prep, 'batch_id', None) or '-',
        'batch_size': getattr(prep, 'batch_size', None),
        'slot_no': getattr(prep, 'slot_no', None),
        'editable': status in EDITABLE_STATUSES,
        **audit_of(post),
    }})


@csrf_exempt
@login_required
def recheck_update_api(request):
    """Change one record's status, stamped with the operator and the time."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    station_id = data.get('machine_id') or machine_id_for_name(data.get('machine_name'))
    new_status = data.get('status')
    prep_id = data.get('prep_id')

    # Authorise before touching anything.
    if not is_machine_allowed(request.user, station_id):
        return JsonResponse(
            {'success': False, 'error': 'You are not assigned to this station.'}, status=403)

    if not station_id or not prep_id or not new_status:
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if new_status not in ALLOWED_NEW_STATUSES:
        return JsonResponse({'success': False, 'error': f'Status {new_status!r} is not allowed'},
                            status=400)

    config = get_config(station_id)
    if not config:
        return JsonResponse({'success': False, 'error': 'Machine not found'}, status=404)

    prep, post = _record_for(config, prep_id)
    if prep is None:
        return JsonResponse({'success': False, 'error': 'Record not found'}, status=404)
    if post is None:
        return JsonResponse(
            {'success': False,
             'error': 'This part has not been judged by the machine yet, so there is '
                      'nothing to recheck.'}, status=409)

    # Only failed / already-reworked parts are the operator's to change.
    current = _post_status(post)
    if current not in EDITABLE_STATUSES:
        return JsonResponse(
            {'success': False,
             'error': f'Only {" or ".join(EDITABLE_STATUSES)} records can be changed; '
                      f'this one is {current}.'}, status=409)

    updated_at = timezone.now()
    post.status = new_status
    post.last_updated_by = request.user.username
    post.last_updated_at = updated_at
    post.save(update_fields=['status', 'last_updated_by', 'last_updated_at'])

    return JsonResponse({
        'success': True,
        'message': f'Status updated to {RECHECK_LABEL if new_status == RECHECK_VALUE else new_status}',
        'new_status': new_status,
        'new_status_label': RECHECK_LABEL if new_status == RECHECK_VALUE else new_status,
        'previous_status': current,
        'updated_by': request.user.username,
        'updated_at': updated_at.isoformat(),
    })
