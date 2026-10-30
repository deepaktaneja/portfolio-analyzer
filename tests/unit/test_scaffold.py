from app.main import main
from models.db import Base, get_session
from models.schemas import HealthCheck


def test_scaffold_imports() -> None:
    assert HealthCheck().status == "ok"
    assert Base is not None


def test_session_can_be_created() -> None:
    session = get_session()
    assert session is not None
    session.close()