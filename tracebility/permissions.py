"""
Operator -> station access control for the Recheck page.

Machines are config dicts in views.py, not database rows, so a station is
identified by the slug derived from its config name. That derivation lives
here so the ids used for assignment can never drift from the ids used for
filtering.
"""
from .models import OperatorAssignment
from .views import ALL_CONFIGS, machine_id


def get_all_machines():
    """All assignable stations as [{'id', 'name', 'type'}], in dashboard order."""
    return [
        {'id': machine_id(config), 'name': config['display_name'], 'type': config['type']}
        for config in ALL_CONFIGS
    ]


def get_machine_choices():
    """[(id, label)] for the admin checkbox grid."""
    return [(m['id'], f"{m['name']}  ({m['type']})") for m in get_all_machines()]


def machine_id_for_name(machine_name):
    """Map a config 'name' (as carried on search results) back to its id, or None."""
    for config in ALL_CONFIGS:
        if config['name'] == machine_name:
            return machine_id(config)
    return None


def get_allowed_machine_ids(user):
    """
    Station ids this user may act on, or None meaning "no restriction".

    None rather than an empty set is the unrestricted signal, so an operator
    assigned to zero stations is locked out rather than handed everything.
    """
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return None

    assignment = OperatorAssignment.objects.filter(user=user).first()
    if assignment is None:
        return None          # never assigned -> unrestricted
    return set(assignment.machines or [])


def is_machine_allowed(user, station_id):
    allowed = get_allowed_machine_ids(user)
    return allowed is None or station_id in allowed


def filter_machines_for_user(user, machines):
    """Narrow a [{'id', ...}] list to what the user may see."""
    allowed = get_allowed_machine_ids(user)
    if allowed is None:
        return machines
    return [m for m in machines if m['id'] in allowed]
