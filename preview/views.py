"""
The whole web layer. It moves bytes in and hands a result to a template.

Deliberately thin. Nothing here knows what a CSV is, and nothing in core/ knows
what a request is. If CSV handling ever appears in this file, the separation
the brief asks for has quietly stopped being real.
"""

from django.shortcuts import render

from core.analysis import analyze_csv
from core.types import InvalidHRISFile

# Guards against someone uploading a 2GB file and waiting for the server to
# read all of it. The whole file is held in memory during analysis, so this is
# the honest place to draw a line.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def upload(request):
    if request.method != "POST":
        return render(request, "preview/upload.html")

    uploaded = request.FILES.get("hris_file")
    if uploaded is None:
        return _upload_error(request, "Choose a CSV file to upload.")

    if uploaded.size > MAX_UPLOAD_BYTES:
        return _upload_error(
            request,
            "That file is larger than 20 MB. OrgLens reads the whole file into "
            "memory, so it is limited to files around that size.",
        )

    try:
        preview = analyze_csv(uploaded.read())
    except InvalidHRISFile as problem:
        # The core raises this with a message written for a non-engineer, so it
        # is shown as-is. Any OTHER exception is a bug in our code, and Django
        # should report it rather than us hiding it behind a friendly message.
        return _upload_error(request, str(problem))

    return render(
        request,
        "preview/result.html",
        {"preview": preview, "filename": uploaded.name},
    )


def _upload_error(request, message):
    """Send the user back to the form with an explanation, not a stack trace."""
    return render(request, "preview/upload.html", {"error": message})
