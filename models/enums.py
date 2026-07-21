"""Source platform enumeration.

A single ``StrEnum`` shared by the domain model, ORM model, scrapers,
and bot handlers -- so the list of supported platforms is defined in
exactly one place.

To add a new job board:
    1. Add a member here
    2. Write the scraper class in ``scrapers/implementations/``
    3. Register the scraper instance in ``main.py``
"""

from enum import StrEnum


class SourcePlatform(StrEnum):
    """Identifies which job board a ``JobPosting`` originated from.

    Uses ``StrEnum`` (Python 3.11+) so values serialize to their string
    representation naturally -- no ``.value`` needed in most contexts.
    """

    WANTEDLY = "wantedly"
    """Wantedly -- startup/tech job platform popular in Japan."""

    MYNAVI_2027 = "mynavi_2027"
    """Mynavi 2027 新卒 (job.mynavi.jp) -- new graduate recruitment."""

    INDEED = "indeed"
    """Indeed Japan (jp.indeed.com) -- aggregator behind Cloudflare.

    There is no local scraper for this platform: Indeed blocks direct
    scraping, so listings are fetched by the Hermes agent (``web_extract``)
    and fed into the pipeline via ``search_cli.py --ingest``.
    """
