"""Domain models -- the canonical representation of job postings.

``JobPosting`` is the single source of truth shared across all layers:
scrapers produce these, repositories persist these, bot handlers format these.
"""

from models.enums import SourcePlatform
from models.job_posting import JobPosting

__all__ = ["SourcePlatform", "JobPosting"]
