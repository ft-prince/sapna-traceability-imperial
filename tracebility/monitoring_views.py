"""
Monitoring page - read-only search across all machines, Excel export, chart data.

Search and export call the same search_monitoring_data with the same params, so
the sheet always holds exactly the rows the table shows.
"""
from datetime import datetime, timedelta
from collections import defaultdict

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .views import (
    ALL_CONFIGS, POST_INDEX_RESTRICT_MAX, _index_posts_by, _match_post, _post_status,
    _restrict_for, _stream_in_window, get_config, machine_id, model_from_qr,
    parse_timestamp_to_datetime, to_iso,
)

# Per-cell borders/alignment dominate the cost of a large export, so they are
# applied only up to this many rows. Data is never dropped - only decoration.
CELL_STYLING_MAX_ROWS = 5000

# Column widths are estimated from this many rows rather than the whole sheet.
COLUMN_WIDTH_SAMPLE_ROWS = 200

DEFAULT_TIME_FILTER = '1hour'
FILTER_KEYS = ('qr_code', 'model_name', 'machine', 'time_filter', 'start_date', 'end_date', 'status')


def filters_from(request):
    filters = {key: request.GET.get(key, '') for key in FILTER_KEYS}
    filters['time_filter'] = filters['time_filter'] or DEFAULT_TIME_FILTER
    return filters


def get_machine_list_monitoring():
    return [{'id': machine_id(c), 'name': c['display_name'], 'type': c['type']} for c in ALL_CONFIGS]


def get_all_model_names_monitoring():
    """Distinct model codes (QR prefix before '#') across every prep table."""
    names = set()
    for config in ALL_CONFIGS:
        try:
            for qr in config['prep_model'].objects.values_list('qr_data', flat=True).distinct():
                names.add(model_from_qr(qr))
        except Exception as exc:
            print(f'Error listing models for {config["name"]}: {exc}')
    names.discard('N/A')
    return sorted(names)


def parse_time_filter(time_filter):
    """'15min' | '30min' | '<N>hour' -> (start, end) aware datetimes. Unknown -> last hour."""
    end_dt = timezone.now()
    try:
        if time_filter.endswith('min'):
            delta = timedelta(minutes=int(time_filter[:-3]))
        elif time_filter.endswith('hour'):
            delta = timedelta(hours=int(time_filter[:-4]))
        else:
            delta = timedelta(hours=1)
    except ValueError:
        delta = timedelta(hours=1)
    return end_dt - delta, end_dt


def _window(filters):
    start, end = filters.get('start_date', ''), filters.get('end_date', '')
    if start and end:
        try:
            start_dt = timezone.make_aware(datetime.strptime(start, '%Y-%m-%d'))
            end_dt = timezone.make_aware(datetime.strptime(end, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
            return start_dt, end_dt
        except ValueError:
            pass
    return parse_time_filter(filters.get('time_filter', DEFAULT_TIME_FILTER))


def search_monitoring_data(filters):
    """
    All records matching the filters, newest first. Each record carries every
    field the UI and the export need; timestamps are ISO strings.
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
        # Let the DB narrow first when a QR / model filter is present.
        query = Q()
        if qr_code:
            query &= Q(qr_data__icontains=qr_code)
        if model_name and model_name != 'all':
            query &= Q(qr_data__startswith=f'{model_name}#')

        # Phase 1: prep rows inside the window (no slicing before the date filter).
        try:
            matched = list(_stream_in_window(
                config['prep_model'].objects.filter(query), lambda p: p.timestamp, start_dt, end_dt,
            ))
        except Exception as exc:
            print(f'Error querying {config["name"]}: {exc}')
            continue

        # Phase 2: one post query per machine - IN (...) when narrow, full scan when broad.
        restrict_to = _restrict_for([p for p, _, _ in matched]) if len(matched) <= POST_INDEX_RESTRICT_MAX else None
        index = _index_posts_by(config['post_model'], restrict_to)

        # Phase 3: output rows, reusing the timestamp parsed in phase 1.
        for prep, _dt, timestamp_iso in matched:
            post = _match_post(index, prep)
            status = _post_status(post)
            if status_filter and status_filter != 'all' and status != status_filter:
                continue
            results.append({
                'prep_id': prep.id,
                'post_id': post.id if post else None,
                'machine_name': config['name'],
                'display_name': config['display_name'],
                'machine_type': config['type'],
                'qr_code': prep.qr_data,
                'model_name': model_from_qr(prep.qr_data),
                'timestamp': timestamp_iso,
                'post_timestamp': to_iso(post.timestamp) if post else '',
                'status': status,
                'previous_machine_status': prep.pervious_status or '-',
                'batch_id': getattr(prep, 'batch_id', None) or (getattr(post, 'batch_id', None) if post else None) or '-',
                'batch_size': getattr(prep, 'batch_size', None),
                'slot_no': getattr(prep, 'slot_no', None),
            })

    # ISO strings sort chronologically; unparseable ('') sink to the bottom.
    results.sort(key=lambda r: r['timestamp'], reverse=True)
    return results


def monitoring_page(request):
    return render(request, 'dashboard/monitoring.html', {
        'model_names': get_all_model_names_monitoring(),
        'machines': get_machine_list_monitoring(),
    })


def monitoring_search_api(request):
    results = search_monitoring_data(filters_from(request))
    return JsonResponse({'success': True, 'count': len(results), 'results': results})


# Every field search_monitoring_data returns has a column here (test_monitoring_export checks).
EXPORT_COLUMNS = [
    ('Prep ID', lambda r: r.get('prep_id') or '-'),
    ('Post ID', lambda r: r.get('post_id') or '-'),
    ('Machine', lambda r: r.get('display_name', 'N/A')),
    ('Machine Type', lambda r: r.get('machine_type', 'N/A')),
    ('QR Code', lambda r: r.get('qr_code', '-')),
    ('Date', lambda r: _split_iso(r.get('timestamp'))[0]),
    ('Time', lambda r: _split_iso(r.get('timestamp'))[1]),
    ('Status', lambda r: r.get('status', 'Pending')),
    ('Model', lambda r: r.get('model_name', 'N/A')),
    ('Previous Status', lambda r: r.get('previous_machine_status', '-')),
    ('Post Date', lambda r: _split_iso(r.get('post_timestamp'))[0]),
    ('Post Time', lambda r: _split_iso(r.get('post_timestamp'))[1]),
    ('Batch ID', lambda r: r.get('batch_id') or '-'),
    ('Batch Size', lambda r: r.get('batch_size') if r.get('batch_size') is not None else '-'),
    ('Slot No', lambda r: r.get('slot_no') if r.get('slot_no') is not None else '-'),
]
STATUS_COLUMN = 8


def _split_iso(timestamp_iso):
    if not timestamp_iso:
        return '-', '-'
    try:
        dt = datetime.fromisoformat(timestamp_iso.replace('Z', '+00:00'))
    except ValueError:
        return timestamp_iso, ''
    return dt.strftime('%Y-%m-%d'), dt.strftime('%H:%M:%S')


def monitoring_export_excel(request):
    """Same params as search, same rows, as .xlsx."""
    results = search_monitoring_data(filters_from(request))

    wb = Workbook()
    ws = wb.active
    ws.title = 'Monitoring Data'

    header_fill = PatternFill(start_color='2E3192', end_color='2E3192', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    status_fills = {
        'OK': PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid'),
        'NG': PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid'),
        'Pending': PatternFill(start_color='FFEB9C', end_color='FFEB9C', fill_type='solid'),
    }

    for col, (header, _) in enumerate(EXPORT_COLUMNS, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill, cell.font, cell.alignment, cell.border = header_fill, header_font, header_alignment, border
    ws.row_dimensions[1].height = 30

    apply_cell_styling = len(results) <= CELL_STYLING_MAX_ROWS
    row_alignment = Alignment(vertical='center')
    bold = Font(bold=True)
    for row, record in enumerate(results, 2):
        for col, (_, getter) in enumerate(EXPORT_COLUMNS, 1):
            cell = ws.cell(row=row, column=col, value=getter(record))
            if apply_cell_styling:
                cell.border, cell.alignment = border, row_alignment
        status_cell = ws.cell(row=row, column=STATUS_COLUMN)
        status_cell.font = bold
        if status_cell.value in status_fills:
            status_cell.fill = status_fills[status_cell.value]

    sample_rows = min(len(results), COLUMN_WIDTH_SAMPLE_ROWS) + 1
    for col, (header, _) in enumerate(EXPORT_COLUMNS, 1):
        letter = get_column_letter(col)
        longest = max(
            [len(header)] + [len(str(c.value)) for c in ws[letter][:sample_rows] if c.value and str(c.value) != '-']
        )
        ws.column_dimensions[letter].width = min(longest + 2, 40)
    ws.freeze_panes = 'F2'

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="monitoring_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    wb.save(response)
    return response


def monitoring_chart_data_api(request):
    results = search_monitoring_data(filters_from(request))
    statuses = ('OK', 'NG', 'REWORK', 'Pending')
    by_time = defaultdict(lambda: dict.fromkeys(statuses, 0))
    by_machine = defaultdict(lambda: dict.fromkeys(statuses, 0))
    by_model = defaultdict(lambda: dict.fromkeys(statuses, 0))

    for record in results:
        dt = parse_timestamp_to_datetime(record['timestamp'])
        time_key = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0).strftime('%H:%M') if dt else 'Unknown'
        status = record['status']
        by_time[time_key][status] += 1
        by_machine[record['display_name']][status] += 1
        if record['model_name'] != 'N/A':
            by_model[record['model_name']][status] += 1

    def series(bucket, keys):
        return {s.lower(): [bucket[k][s] for k in keys] for s in statuses}

    times = sorted(by_time)
    return JsonResponse({'success': True, 'data': {
        'time_series': {'labels': times, **series(by_time, times)},
        'machine_breakdown': {'machines': list(by_machine), **series(by_machine, list(by_machine))},
        'model_breakdown': {'models': list(by_model), **series(by_model, list(by_model))},
        'summary': {
            'total': len(results),
            **{s.lower(): sum(r['status'] == s for r in results) for s in statuses},
        },
    }})
