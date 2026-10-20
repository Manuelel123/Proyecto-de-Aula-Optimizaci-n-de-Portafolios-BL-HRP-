"""The educational notice, also stamped on downloadable reports (and CSV, in the templates)."""

import re

EDUCATIONAL_NOTICE = "Información educativa. No es recomendación de inversión ni asesoría financiera."

_BODY_TAG = re.compile(rb"<body[^>]*>", re.IGNORECASE)
_BANNER = (
    '<div style="margin:0;padding:12px 16px;background:#fffbeb;color:#92400e;'
    'border-bottom:1px solid #fde68a;font:600 14px/1.4 system-ui,sans-serif">'
    f"{EDUCATIONAL_NOTICE}</div>"
).encode("utf-8")


def with_notice_html(report: bytes) -> bytes:
    """``report`` (an HTML document) with the notice right after ``<body>``."""
    match = _BODY_TAG.search(report)
    if match is None:
        return _BANNER + report
    return report[: match.end()] + _BANNER + report[match.end() :]
