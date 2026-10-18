"""Create and remove a disposable MySQL database to test migration and Go concurrency.

Run from ai-py; requires MIGRATION_TEST_ADMIN_USER and MIGRATION_TEST_ADMIN_PASSWORD.
Never reads or modifies the configured business database's tables.
"""
import os,sys,uuid,subprocess
sys.path.insert(0, os.getcwd())
from app.config import settings
from sqlalchemy import create_engine,text
from sqlalchemy.engine import URL
from scripts.migrate_ticket_idempotency import migrate
from app.models import Ticket
from app.db import Base
import pymysql
host=settings.mysql_host
port=settings.mysql_port
user=os.environ['MIGRATION_TEST_ADMIN_USER']; password=os.environ['MIGRATION_TEST_ADMIN_PASSWORD']
name='devsupport_ticket_test_'+uuid.uuid4().hex[:10]
admin=pymysql.connect(host=host,port=port,user=user,password=password,autocommit=True)
engine=None
try:
 with admin.cursor() as c:c.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
 engine=create_engine(URL.create('mysql+pymysql',username=user,password=password,host=host,port=port,database=name))
 # Simulate existing schema and legacy rows, then exercise restartability.
 with engine.begin() as c:
  c.execute(text('CREATE TABLE ticket (ticket_id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL)'))
  c.execute(text("INSERT INTO ticket VALUES ('legacy1','legacy'),('legacy2','legacy')"))
 migrate(engine); migrate(engine)
 with engine.connect() as c:
  assert c.execute(text('SELECT COUNT(*) FROM ticket WHERE idempotency_key IS NULL')).scalar()==2
 print('PASS migration twice preserves legacy rows')
 # Replace isolated legacy fixture with the current complete schema for Go tests.
 with engine.begin() as c:c.execute(text('DROP TABLE ticket'))
 Ticket.__table__.create(engine)
 env=dict(os.environ,GOCACHE='/tmp/devassist-go-cache',TEST_MYSQL_DSN=f'{user}:{password}@tcp({host}:{port})/{name}?charset=utf8mb4&parseTime=true&loc=UTC')
 subprocess.run(['go','test','-race','./internal/service','-v'],cwd='../backend-go',env=env,check=True)
finally:
 if engine:engine.dispose()
 with admin.cursor() as c:c.execute(f'DROP DATABASE IF EXISTS `{name}`')
 admin.close()
