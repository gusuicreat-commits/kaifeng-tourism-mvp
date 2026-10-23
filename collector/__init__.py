"""Kaifeng main-district tourism data collection toolkit.

Engineering infrastructure only: it registers sources, records runs and
failures, extracts structured candidates and validates them. It never writes
to the runtime fact store (data/tourism.sqlite3); city-facts/1 bundles are
produced for review and are applied by an explicit human decision through
scripts/import_city_facts.py.
"""

__all__ = ["config", "ledger", "fetch", "discover", "extract", "validate", "bundle", "quality", "report"]
