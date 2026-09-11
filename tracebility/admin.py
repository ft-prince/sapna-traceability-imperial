from django.contrib import admin
from django.utils.html import format_html
from . import models


def colored_status(status):
    colors = {'OK': ('#e8f5e9', '#00cc66', '✓'), 'NG': ('#ffebee', '#cc3333', '✗')}
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
