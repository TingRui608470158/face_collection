from django.urls import path
from . import views

urlpatterns = [
    path('', views.name_role_form, name='name_role_form'),
    path('collect/', views.collect, name='collect'),
    path('upload/', views.upload_frame, name='upload_frame'),  # 保留但不再使用
    path('collect/reset/', views.reset_batch, name='reset_batch'),
    path('select/', views.select_image, name='select_image'),
    path('complete/', views.complete, name='complete'),
    path('finalize/', views.finalize, name='finalize'),
    # Console
    path('console/employees/', views.console_employees, name='console_employees'),
    path('console/employees/<str:employee_id>/', views.console_employee_detail, name='console_employee_detail'),
    path('console/employees/delete/<str:employee_id>/', views.console_employee_delete, name='console_employee_delete'),
    path('console/visitors/', views.console_visitors, name='console_visitors'),
    path('console/visitors/<str:visitor_index>/', views.console_visitor_detail, name='console_visitor_detail'),
    path('console/visitors/delete/<str:visitor_index>/', views.console_visitor_delete, name='console_visitor_delete'),
]


