from noticom_node.control import ws_url


def test_ws_url():
    assert ws_url("http://10.0.4.69:8123") == "ws://10.0.4.69:8123/api/noticom/ws"
    assert ws_url("https://ha.example:8123/") == "wss://ha.example:8123/api/noticom/ws"
