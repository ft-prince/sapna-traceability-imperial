"""
LOCAL DEV ONLY - seed the restored Imperial_trace copy with realistic volume so
the >1000-row checks mean something. Never run against the plant database:
Node-RED is the only writer there.

Run:  ./venv/bin/python sample_data.py [rows_per_machine]
Keeps existing rows; appends after the current max id in chronological order.
"""
import os
import random
import sys
from datetime import datetime, timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from tracebility import models  # noqa: E402

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
MODELS = ['68603833AA', '68603834BB', '71200450CC']
FMT = '%Y-%m-%d %H:%M:%S'  # exactly what Node-RED writes for this plant

MACHINES = [
    ('Camera Inspection', models.CiPreprocessing, models.CiPostprocessing, 2, 0),
    ('Autofatash', models.AutoPreprocessing, models.AutoPostprocessing, 1, 1),
    ('Helium Station', models.HeliumPreprocessing, models.HeliumPostprocessing, 1, 0),
]


def seed(name, prep_model, post_model, multiplier, is_auto):
    random.seed(name)
    count = ROWS * multiplier
    now = datetime.now().replace(microsecond=0)
    # Spread over ~60 days, ending a minute ago so the machine shows ACTIVE.
    start = now - timedelta(days=60)
    step = (now - timedelta(minutes=1) - start) / count
    preps, posts = [], []
    next_pre_id = (prep_model.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
    for i in range(count):
        loaded = start + step * i
        qr = f'{random.choice(MODELS)}#{random.randint(100, 260)}#2026#{random.randint(0, 9999):04d}'
        extra = {}
        if is_auto:
            extra = {'batch_id': f'AF-{loaded.strftime("%Y%m%d%H%M%S")}-{random.randint(100, 999)}',
                     'batch_size': 1, 'slot_no': random.randint(1, 4)}
        preps.append(prep_model(id=next_pre_id + i, timestamp=loaded.strftime(FMT), machine_name=name,
                                qr_data=qr, pervious_status=random.choice(['OK'] * 9 + ['NG']), **extra))
        roll = random.random()
        if roll < 0.05:
            continue  # still pending (no post row)
        status = 'NG' if roll < 0.15 else 'OK'
        post_extra = {'batch_id': extra['batch_id']} if is_auto else {}
        posts.append(post_model(timestamp=(loaded + timedelta(seconds=random.randint(5, 40))).strftime(FMT),
                                qr_data=qr, status=status, pre_id=next_pre_id + i, **post_extra))
    prep_model.objects.bulk_create(preps, batch_size=2000)
    post_model.objects.bulk_create(posts, batch_size=2000)
    print(f'{name}: +{len(preps)} prep, +{len(posts)} post -> {prep_model.objects.count()} / {post_model.objects.count()}')


if __name__ == '__main__':
    for machine in MACHINES:
        seed(*machine)
