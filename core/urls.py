from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/invoices/", views.invoice_create, name="invoice_create"),
    path(
        "api/invoices/<uuid:invoice_id>/status/",
        views.invoice_status,
        name="invoice_status",
    ),
]
