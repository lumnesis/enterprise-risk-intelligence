from sqlalchemy import Engine, create_engine

from enterprise_risk.config import database_url


def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 10})
