from alembic import context
from sqlalchemy import engine_from_config, pool

from packages.common.config import get_settings
from packages.db import Base

config = context.config
# Alembic ConfigParser treats percent signs as interpolation syntax.
# Escape them here; get_main_option restores the original DSN for SQLAlchemy.
config.set_main_option("sqlalchemy.url", get_settings().postgres_dsn.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
