from django import template

register = template.Library()

# Group buttons on the dashboard. Keyed by substring of the machine id, which
# is derived from the config name (see views.machine_id).
GROUPS = (('camera_inspection', 'inspection'), ('autofatash', 'auto'), ('helium', 'helium'))


@register.filter(name='get_machine_group')
def get_machine_group(machine_id):
    """Map a machine id to its dashboard group."""
    machine_id = str(machine_id).lower()
    for needle, group in GROUPS:
        if needle in machine_id:
            return group
    return 'other'
