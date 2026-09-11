from django import forms
from django.contrib import admin
from django.utils.html import format_html
from . import models
from .permissions import get_machine_choices


def colored_status(status):
    colors = {'OK': ('#e8f5e9', '#00cc66', '✓'), 'NG': ('#ffebee', '#cc3333', '✗'),
              'REWORK': ('#fff8e1', '#f57c00', '🔁')}
    bg, fg, icon = colors.get(status, ('#fff3e0', '#ff9933', '⏳'))
    return format_html(
        '<span style="background:{};color:{};padding:4px 12px;border-radius:4px;font-weight:bold;">{} {}</span>',
        bg, fg, icon, status,
    )


class PrepAdmin(admin.ModelAdmin):
    list_display = ('id', 'timestamp', 'machine_name', 'qr_data', 'pervious_status')
    search_fields = ('qr_data', 'machine_name')
    list_filter = ('machine_name', 'pervious_status')
    readonly_fields = ('id',)


class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'timestamp', 'qr_data', 'status_display', 'pre_id')
    search_fields = ('qr_data',)
    list_filter = ('status',)
    readonly_fields = ('id',)

    @admin.display(description='Status')
    def status_display(self, obj):
        return colored_status(obj.status)


@admin.register(models.AutoPreprocessing)
class AutoPrepAdmin(PrepAdmin):
    list_display = PrepAdmin.list_display + ('batch_id', 'batch_size', 'slot_no')


@admin.register(models.AutoPostprocessing)
class AutoPostAdmin(PostAdmin):
    list_display = PostAdmin.list_display + ('batch_id',)


admin.site.register(models.CiPreprocessing, PrepAdmin)
admin.site.register(models.CiPostprocessing, PostAdmin)
admin.site.register(models.HeliumPreprocessing, PrepAdmin)
admin.site.register(models.HeliumPostprocessing, PostAdmin)


class OperatorAssignmentForm(forms.ModelForm):
    """Renders the JSON `machines` list as a checkbox grid."""
    machines = forms.MultipleChoiceField(
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Assigned stations',
        help_text='Tick every station this operator may recheck. '
                  'Leave all unticked to block the operator from every station.',
    )

    class Meta:
        model = models.OperatorAssignment
        fields = ('user', 'machines')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Evaluated per instance, so machine config changes appear without a restart.
        self.fields['machines'].choices = get_machine_choices()


@admin.register(models.OperatorAssignment)
class OperatorAssignmentAdmin(admin.ModelAdmin):
    form = OperatorAssignmentForm
    list_display = ('user', 'station_count', 'assigned_stations', 'updated_at')
    search_fields = ('user__username',)
    readonly_fields = ('updated_at',)

    @admin.display(description='Stations')
    def station_count(self, obj):
        return len(obj.machines or [])

    @admin.display(description='Assigned stations')
    def assigned_stations(self, obj):
        labels = dict(get_machine_choices())
        names = [labels.get(m, m).split('  (')[0] for m in (obj.machines or [])]
        if not names:
            return format_html('<span style="color: #cc3333;">No access</span>')
        return ', '.join(names)

    # Superusers only. Operators are is_staff, so a stray change permission
    # would otherwise let them widen their own station access.
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
