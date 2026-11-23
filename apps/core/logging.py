"""Redact public bearer tokens before operational log aggregation."""

import logging
import re

_TOKEN_PATH = re.compile(r"/(p|sas/confirm|followup|badges/verify)/[^/\s?]+")


class RedactBearerPaths(logging.Filter):
    def filter(self, record):
        record.msg = _TOKEN_PATH.sub(r"/\1/[redacted]", record.getMessage())
        record.args = ()
        return True
