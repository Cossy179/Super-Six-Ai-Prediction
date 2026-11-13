"""Versioned REST API over stored rounds and forecasts (DESIGN.md §15).

Read endpoints serve what the prediction service already stored: nothing is re-forecast on
a GET. Admin endpoints are off unless ``SUPER6_ADMIN_TOKEN`` is set.

Run it with ``super6 serve`` (needs the ``api`` extra: ``pip install "super6-ai[api]"``).
"""
