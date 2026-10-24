"""Schema import point for perception. Uses shared/schema.py once it exists."""

try:
    from shared.schema import LineItem, PayRequest, SiteEvidence
except ImportError:
    from perception._provisional_schema import LineItem, PayRequest, SiteEvidence

__all__ = ["LineItem", "PayRequest", "SiteEvidence"]
