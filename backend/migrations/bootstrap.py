from src.config.database import Base, engine
from src.models import account, correction, dataset, review, run  # noqa: F401


def bootstrap():
    Base.metadata.create_all(engine)
