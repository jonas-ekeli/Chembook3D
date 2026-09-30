"""Alembic environment. Migrations always run programmatically on a connection handed in by
chembook3d.investigation (P21); there is no alembic.ini."""

from alembic import context

from chembook3d.models import Base

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
