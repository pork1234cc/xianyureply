"""本机真实 TLS 隧道验证 HTTP/HTTPS 代理特殊字符认证。"""

import asyncio
import base64
import ipaddress
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from websockets.asyncio.server import serve

from goofish_bridge.network import NetworkProfile


@pytest.fixture
def tls_contexts(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(days=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return server, client


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_encoded_credentials_work_through_real_tls_tunnel(tls_contexts, scheme):
    server_tls, client_tls = tls_contexts
    seen, handlers = [], set()

    async def echo(connection):
        await connection.send(await connection.recv())

    async with serve(echo, "127.0.0.1", 0, ssl=server_tls) as target:
        target_port = target.sockets[0].getsockname()[1]

        async def relay(reader, writer):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()

        async def handle(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            upstream = None
            pumps = []
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                seen.append(request)
                upstream_reader, upstream = await asyncio.open_connection("127.0.0.1", target_port)
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                pumps = [asyncio.create_task(relay(reader, upstream)),
                         asyncio.create_task(relay(upstream_reader, writer))]
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                writer.close()
                if upstream:
                    upstream.close()
                handlers.discard(task)

        async with await asyncio.start_server(handle, "127.0.0.1", 0,
                                              ssl=server_tls if scheme == "https" else None) as proxy:
            port = proxy.sockets[0].getsockname()[1]
            network = NetworkProfile(mode="proxy", proxy_url=f"{scheme}://u%40name:p%3A%2F@127.0.0.1:{port}")
            options = {"ssl": client_tls, "open_timeout": 3, "close_timeout": 1}
            if scheme == "https":
                options["proxy_ssl"] = client_tls
            async with network.websocket(f"wss://127.0.0.1:{target_port}/", **options) as connection:
                await connection.send("测试隧道")
                assert await connection.recv() == "测试隧道"
            if handlers:
                await asyncio.wait_for(asyncio.gather(*handlers), 3)
    expected = b"Proxy-Authorization: Basic " + base64.b64encode(b"u@name:p:/")
    assert len(seen) == 1 and expected in seen[0]
