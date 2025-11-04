from src.clients.gw import make_bearer_for_user


def test_make_bearer_removes_padding_and_has_prefix():
    bearer = make_bearer_for_user(1)
    assert bearer.startswith("Bearer t_")
    token = bearer.split(" ", 1)[1]
    assert not token.endswith("=")
    assert token == "t_eyJ1aWQiOjF9"
