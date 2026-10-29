"""Schema migration entrypoint usable from both application lifespan and the CLI."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


if __name__ == "__main__":
    from app.config import Settings
    from app.db import make_engine

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    migrate(engine)
    engine.dispose()
