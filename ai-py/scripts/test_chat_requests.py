"""Zero-model integration suite in a disposable MySQL database.

Run from ai-py with MIGRATION_TEST_ADMIN_USER/PASSWORD set. The supplied account
must be able to create/drop test databases. No business database is modified.
"""
import os
import subprocess
import uuid

import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.config import settings
from app.db import Base
from app.models import ChatRequest
from scripts.migrate_chat_requests import migrate


def main():
    user = os.environ["MIGRATION_TEST_ADMIN_USER"]
    password = os.environ["MIGRATION_TEST_ADMIN_PASSWORD"]
    name = "devsupport_chat_test_" + uuid.uuid4().hex[:12]
    admin = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=user, password=password, autocommit=True,
    )
    engine = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        engine = create_engine(URL.create(
            "mysql+pymysql", username=user, password=password,
            host=settings.mysql_host, port=settings.mysql_port, database=name,
        ))
        Base.metadata.create_all(engine, tables=[
            table for table in Base.metadata.sorted_tables if table is not ChatRequest.__table__
        ])
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE migration_sentinel (id INT PRIMARY KEY)"))
            conn.execute(text("INSERT INTO migration_sentinel VALUES (1)"))
        migrate(engine)
        migrate(engine)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM migration_sentinel")).scalar() == 1
        print("PASS additive chat migration is restartable and preserves existing tables", flush=True)
        env = dict(os.environ)
        env.setdefault("GOCACHE", "/tmp/devassist-go-cache")
        env["TEST_MYSQL_DSN"] = (
            f"{user}:{password}@tcp({settings.mysql_host}:{settings.mysql_port})/{name}"
            "?charset=utf8mb4&parseTime=true&loc=UTC"
        )
        subprocess.run(
            ["go", "test", "-race", "./internal/api", "./internal/service", "-v"],
            cwd="../backend-go", env=env, check=True,
        )
    finally:
        if engine is not None:
            engine.dispose()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


if __name__ == "__main__":
    main()
