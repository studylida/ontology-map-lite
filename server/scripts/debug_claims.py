# server/scripts/debug_claims.py
from sqlalchemy.orm import Session
from ontology_map.db.session import get_engine
from ontology_map.db.schema import Claim, Document

with Session(get_engine()) as session:
    claims = session.query(Claim).filter(Claim.id >= 289).all()
    for c in claims:
        print(f"[{c.id}] doc={c.document_id}")
        print(f"  statement: {c.statement}")
        print(f"  quote: {c.quote_text}\n")
