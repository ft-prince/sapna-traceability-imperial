"""
LOCAL DEMO ONLY - insert rows the way Node-RED does, so a recording shows the
machines ACTIVE (green dot) and the dashboard/modal updating live over SSE.

Never run this against the plant database: Node-RED is the only writer there.

Run:   ./venv/bin/python demo_feed.py            # a part every 8s, forever
       ./venv/bin/python demo_feed.py 4          # a part every 4s
       ./venv/bin/python demo_feed.py 4 20       # 20 parts, then stop
Stop:  Ctrl-C
"""
import os
import random
import sys
import time
from datetime import datetime, timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from tracebility import models  # noqa: E402
from tracebility.views import ALL_CONFIGS  # noqa: E402

FMT = '%Y-%m-%d %H:%M:%S'  # exactly what Node-RED writes for this plant
MODELS = ['68603833AA', '68603834BB', '71200450CC']
NG_CHANCE = 0.12      # roughly one NG in eight, so the NG tile moves on camera
PENDING_SECONDS = 3   # prep row appears first, post row lands a moment later


def new_part():
    return f'{random.choice(MODELS)}#{random.randint(100, 260)}#2026#{random.randint(0, 9999):04d}'


def feed_one(qr):
    """Walk one part down the line, leaving each machine Pending then judged."""
    for config in ALL_CONFIGS:
        now = datetime.now().replace(microsecond=0)
        prep_model, post_model = config['prep_model'], config['post_model']

        extra = {}
        if prep_model is models.AutoPreprocessing:
            extra = {'batch_id': f'AF-{now.strftime("%Y%m%d%H%M%S")}-{random.randint(100, 999)}',
                     'batch_size': 1, 'slot_no': random.randint(1, 4)}

        prep = prep_model.objects.create(
            timestamp=now.strftime(FMT), machine_name=config['display_name'],
            qr_data=qr, pervious_status='OK', **extra)
        print(f'  {config["display_name"]:20s} loaded  {qr}')

        # Pause so the card visibly sits on PENDING before the result lands.
        time.sleep(PENDING_SECONDS)

        status = 'NG' if random.random() < NG_CHANCE else 'OK'
        post_extra = {'batch_id': extra['batch_id']} if extra else {}
        post_model.objects.create(
            timestamp=(now + timedelta(seconds=PENDING_SECONDS)).strftime(FMT),
            qr_data=qr, status=status, pre_id=prep.id, **post_extra)
        print(f'  {config["display_name"]:20s} judged  {status}')


if __name__ == '__main__':
    gap = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    print(f'Feeding a part every {gap}s'
          f'{f" ({limit} parts)" if limit else " (Ctrl-C to stop)"}\n')
    made = 0
    try:
        while limit is None or made < limit:
            qr = new_part()
            print(f'part {made + 1}: {qr}')
            feed_one(qr)
            made += 1
            time.sleep(gap)
    except KeyboardInterrupt:
        print(f'\nstopped after {made} parts')
