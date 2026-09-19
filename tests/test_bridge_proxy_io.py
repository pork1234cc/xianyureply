"""本机代理协议验证：真实客户端连接本机，不访问闲鱼或外网。"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import StreamRequestHandler, ThreadingTCPServer

import pytest
import requests
from websockets.exceptions import InvalidProxyStatus, ProxyError

from goofish_bridge.network import NetworkProfile


@pytest.fixture
def proxy_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802
            received.append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Length", "8")
            self.end_headers()
            self.wfile.write(b"proxy-ok")

        def do_CONNECT(self):  # noqa: N802
            received.append((self.command, self.path))
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_real_http_and_wss_use_proxy_and_do_not_fall_back(proxy_server, monkeypatch):
    address, received = proxy_server
    monkeypatch.setenv("TEST_PROXY", address)
    profile = NetworkProfile(mode="proxy", proxy_url_env="TEST_PROXY")
    with profile.http() as client:
        assert client.get("http://target.invalid/example", timeout=2).text == "proxy-ok"
        with pytest.raises(requests.exceptions.ProxyError):
            client.get("https://target.invalid/example", timeout=2)
    with pytest.raises(InvalidProxyStatus):
        async with profile.websocket("wss://target.invalid/", open_timeout=2):
            pytest.fail("代理拒绝后不应建立连接")
    assert received == [("GET", "http://target.invalid/example"),
                        ("CONNECT", "target.invalid:443"), ("CONNECT", "target.invalid:443")]


@pytest.mark.asyncio
async def test_socks_authentication_and_remote_dns_for_http_and_ws():
    received = []

    class Handler(StreamRequestHandler):
        def handle(self):
            version, length = self.rfile.read(2)
            methods = self.rfile.read(length)
            if version != 5 or 2 not in methods:
                return
            self.wfile.write(b"\x05\x02")
            version, length = self.rfile.read(2)
            username = self.rfile.read(length)
            password = self.rfile.read(self.rfile.read(1)[0])
            self.wfile.write(b"\x01\x00")
            request = self.rfile.read(4)
            if request != b"\x05\x01\x00\x03":
                return
            host = self.rfile.read(self.rfile.read(1)[0])
            port = int.from_bytes(self.rfile.read(2), "big")
            received.append((username, password, host, port))
            # 明确拒绝目标连接，客户端必须报错，不能另走直连。
            self.wfile.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")

    with ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            profile = NetworkProfile(mode="proxy", proxy_url=
                f"socks5://u%40name:p%3A%2F@127.0.0.1:{server.server_address[1]}")
            with profile.http() as client, pytest.raises(requests.exceptions.ConnectionError):
                client.get("https://target.invalid/", timeout=2)
            with pytest.raises(ProxyError):
                async with profile.websocket("wss://target.invalid/", open_timeout=2):
                    pytest.fail("代理拒绝后不应建立连接")
            assert received == [(b"u@name", b"p:/", b"target.invalid", 443)] * 2
        finally:
            server.shutdown()
            thread.join(timeout=5)
