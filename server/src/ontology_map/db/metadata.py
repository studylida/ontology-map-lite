"""Complete SQLAlchemy metadata for migrations, schema tests, and generated docs."""

import sys
from pathlib import Path

_SERVER_SRC = str(Path(__file__).resolve().parents[2])
if _SERVER_SRC not in sys.path:
    sys.path.insert(0, _SERVER_SRC)

from ontology_map.db.promotion_provenance import (  # noqa: E402
    promotion_canonical_change,
)
from ontology_map.db.schema import metadata  # noqa: E402

__all__ = ("metadata", "promotion_canonical_change")
