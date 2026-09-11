from django.urls import path
from . import views, monitoring_views, recheck_views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('machine/<str:machine_name>/', views.machine_detail_view, name='machine_detail'),
    path('api/machine/<str:machine_name>/', views.machine_data_api, name='machine_data_api'),
    path('api/search/', views.search_qr_code, name='search_qr'),
    path('stream/dashboard/', views.sse_dashboard_stream, name='sse_dashboard'),
    path('stream/machine/<str:machine_name>/', views.sse_machine_stream, name='sse_machine'),
    path('export/<str:machine_name>/', views.export_machine_data, name='export_machine_data'),
    path('monitoring/', monitoring_views.monitoring_page, name='monitoring_page'),
    path('monitoring/search/', monitoring_views.monitoring_search_api, name='monitoring_search_api'),
    path('monitoring/export-excel/', monitoring_views.monitoring_export_excel, name='monitoring_export_excel'),
    path('monitoring/chart-data/', monitoring_views.monitoring_chart_data_api, name='monitoring_chart_data_api'),

    # Recheck - the only write path in the app, all endpoints login-required
    path('recheck/', recheck_views.recheck_page, name='recheck_page'),
    path('recheck/api/search/', recheck_views.recheck_search_api, name='recheck_search_api'),
    path('recheck/api/update/', recheck_views.recheck_update_api, name='recheck_update_api'),
    path('recheck/api/record/<str:machine_name>/<int:prep_id>/',
         recheck_views.recheck_get_record_api, name='recheck_get_record_api'),
]
