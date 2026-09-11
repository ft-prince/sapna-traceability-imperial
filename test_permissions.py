"""
Self-check for operator -> station access control on the Recheck page.

Run:  ./venv/bin/python test_permissions.py
Touches the database: creates throwaway users and removes them again.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import AnonymousUser, User  # noqa: E402
from django.test import Client  # noqa: E402

from tracebility.models import OperatorAssignment  # noqa: E402
from tracebility.permissions import (  # noqa: E402
    filter_machines_for_user, get_all_machines, get_allowed_machine_ids,
    get_machine_choices, is_machine_allowed, machine_id_for_name,
)
from tracebility.recheck_views import get_machine_list  # noqa: E402
from tracebility.views import ALL_CONFIGS  # noqa: E402

TEST_USERNAME = '__perm_selfcheck__'


def _make_user(is_superuser=False):
    User.objects.filter(username=TEST_USERNAME).delete()
    return User.objects.create_user(
        TEST_USERNAME, password='x', is_staff=True, is_superuser=is_superuser)


def _client(user):
    c = Client(SERVER_NAME='localhost')
    c.force_login(user)
    return c


def test_assignment_ids_match_the_ids_recheck_filters_on():
    # If these drift, assignments silently match nothing and the operator
    # sees an empty page. This is the check that catches that.
    assignable = {m['id'] for m in get_all_machines()}
    filterable = {m['id'] for m in get_machine_list()}
    assert assignable == filterable, f'id drift: {assignable ^ filterable}'


def test_every_machine_name_maps_back_to_its_id():
    ids = {m['id'] for m in get_all_machines()}
    for config in ALL_CONFIGS:
        mapped = machine_id_for_name(config['name'])
        assert mapped in ids, f"{config['name']} mapped to {mapped!r}"


def test_unknown_machine_name_is_not_allowed():
    user = _make_user()
    OperatorAssignment.objects.create(user=user, machines=[get_all_machines()[0]['id']])
    assert machine_id_for_name('no-such-machine') is None
    assert is_machine_allowed(user, None) is False


def test_superuser_is_unrestricted():
    user = _make_user(is_superuser=True)
    assert get_allowed_machine_ids(user) is None
    assert is_machine_allowed(user, 'anything') is True


def test_user_without_assignment_is_unrestricted():
    assert get_allowed_machine_ids(_make_user()) is None


def test_empty_assignment_locks_out_rather_than_grants_all():
    user = _make_user()
    OperatorAssignment.objects.create(user=user, machines=[])
    assert get_allowed_machine_ids(user) == set()
    assert is_machine_allowed(user, get_all_machines()[0]['id']) is False


def test_assigned_user_sees_only_their_stations():
    user = _make_user()
    picked = [get_all_machines()[0]['id'], get_all_machines()[2]['id']]
    OperatorAssignment.objects.create(user=user, machines=picked)
    assert get_allowed_machine_ids(user) == set(picked)
    visible = {m['id'] for m in filter_machines_for_user(user, get_machine_list())}
    assert visible == set(picked)


def test_anonymous_gets_nothing():
    assert get_allowed_machine_ids(AnonymousUser()) == set()


def test_machine_choices_cover_every_station():
    assert {c[0] for c in get_machine_choices()} == {m['id'] for m in get_all_machines()}


def test_search_results_are_filtered_not_just_the_dropdown():
    # The station id arrives as a query parameter, so asking for an
    # unassigned station directly must still return nothing.
    user = _make_user()
    allowed_id = get_all_machines()[0]['id']
    forbidden_id = get_all_machines()[1]['id']
    OperatorAssignment.objects.create(user=user, machines=[allowed_id])
    client = _client(user)

    everything = client.get('/recheck/api/search/', {'machine': 'all'}).json()
    assert all(r['machine_id'] == allowed_id for r in everything['results'])

    forbidden = client.get('/recheck/api/search/', {'machine': forbidden_id}).json()
    assert forbidden['count'] == 0, f"leaked {forbidden['count']} rows from an unassigned station"


def test_unassigned_station_returns_403_on_update_and_detail():
    user = _make_user()
    allowed_id = get_all_machines()[0]['id']
    forbidden_id = get_all_machines()[1]['id']
    OperatorAssignment.objects.create(user=user, machines=[allowed_id])
    client = _client(user)

    detail = client.get(f'/recheck/api/record/{forbidden_id}/1/')
    assert detail.status_code == 403, f'record detail returned {detail.status_code}'

    update = client.post('/recheck/api/update/',
                         data={'machine_id': forbidden_id, 'prep_id': 1, 'status': 'OK'},
                         content_type='application/json')
    assert update.status_code == 403, f'update returned {update.status_code}'


def test_logged_out_is_redirected_from_every_endpoint():
    client = Client(SERVER_NAME='localhost')
    station = get_all_machines()[0]['id']
    for method, url in [
        ('get', '/recheck/'),
        ('get', '/recheck/api/search/'),
        ('get', f'/recheck/api/record/{station}/1/'),
        ('post', '/recheck/api/update/'),
    ]:
        response = getattr(client, method)(url)
        assert response.status_code == 302 and '/accounts/login/' in response['Location'], \
            f'{url} returned {response.status_code}'


if __name__ == '__main__':
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith('test_'):
            continue
        try:
            fn()
            print(f'ok  {name}')
        except AssertionError as exc:
            failures.append(name)
            print(f'FAIL {name}: {exc}')
        finally:
            User.objects.filter(username=TEST_USERNAME).delete()
    print('\n' + ('All permission checks passed.' if not failures else f'{len(failures)} FAILED'))
    raise SystemExit(1 if failures else 0)
