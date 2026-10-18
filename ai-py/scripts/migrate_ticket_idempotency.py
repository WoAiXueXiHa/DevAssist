"""Non-destructive ticket migration. Run: python -m scripts.migrate_ticket_idempotency.

Deploy before the updated Go service. Existing tickets retain NULL keys.
"""

from sqlalchemy import inspect, text


def migrate(engine):
    # MySQL DDL commits implicitly. Both steps are individually restartable.
    with engine.connect() as conn:
        locked = conn.execute(text("SELECT GET_LOCK('devsupport_ticket_idempotency', 30)")).scalar()
        if locked != 1:
            raise RuntimeError("Could not acquire ticket migration lock")
        try:
            inspector = inspect(conn)
            if "idempotency_key" not in {c["name"] for c in inspector.get_columns("ticket")}:
                conn.execute(text("ALTER TABLE ticket ADD COLUMN idempotency_key VARCHAR(128) NULL"))
            indexes = inspect(conn).get_indexes("ticket")
            if not any(i["name"] == "uq_ticket_tenant_idempotency" for i in indexes):
                conn.execute(text("ALTER TABLE ticket ADD UNIQUE INDEX uq_ticket_tenant_idempotency (tenant_id, idempotency_key)"))
            conn.commit()
        finally:
            conn.execute(text("SELECT RELEASE_LOCK('devsupport_ticket_idempotency')"))


if __name__ == "__main__":
    from app.db import sync_engine

    migrate(sync_engine)
    print("Ticket idempotency migration complete.")
