"""Root URL config. Everything lives under the preview app."""

from django.urls import include, path

urlpatterns = [
    path("", include("preview.urls")),
]
