from django.shortcuts import render


def upload(request):
    return render(request, "preview/upload.html")
