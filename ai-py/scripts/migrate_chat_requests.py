"""Add durable chat request claims without touching existing business tables.

Run before deploying updated services: python -m scripts.migrate_chat_requests.
"""
from sqlalchemy import text

from app.models import ChatRequest


def migrate(engine):
    with engine.connect() as conn:
        locked = conn.execute(text("SELECT GET_LOCK('devsupport_chat_requests', 30)")).scalar()
        if locked != 1:
            raise RuntimeError("Could not acquire chat request migration lock")
        try:
            ChatRequest.__table__.create(conn, checkfirst=True)
            conn.commit()
        finally:
            conn.execute(text("SELECT RELEASE_LOCK('devsupport_chat_requests')"))


if __name__ == "__main__":
    from app.db import sync_engine

    migrate(sync_engine)
    print("Chat request migration complete.")
