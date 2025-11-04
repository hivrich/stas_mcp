import pytest

from src.linking import reset, set_pending, set_linked
from src.linking.context import LinkingRequired, get_linked_user_id


@pytest.fixture(autouse=True)
def reset_store() -> None:
    reset()
    yield
    reset()


def test_get_linked_user_id_requires_linking():
    connection_id = "conn-1"
    set_pending(connection_id)
    with pytest.raises(LinkingRequired):
        get_linked_user_id(connection_id)


def test_get_linked_user_id_returns_user_when_linked():
    connection_id = "conn-2"
    set_linked(connection_id, 99)
    assert get_linked_user_id(connection_id) == 99
