"""图片格式、下载上限和目标账号上传的离线验证。"""

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from goofish_bridge import media


def picture(kind="PNG"):
    output = BytesIO()
    Image.new("RGB", (12, 20), "white").save(output, format=kind)
    return output.getvalue()


class Response:
    status_code = 200

    def __init__(self, raw=None, data=b"", status=200):
        self.raw, self.data, self.status_code = raw, data, status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def json(self):
        return self.raw

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.data


@pytest.mark.parametrize("kind,extension,mime", [
    ("PNG", "png", "image/png"), ("JPEG", "jpg", "image/jpeg"),
])
def test_formats_keep_original_bytes_and_account_session(kind, extension, mime):
    data = picture(kind)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"object": {"url": "https://img.alicdn.com/test", "pix": "12x20"}})

    session = SimpleNamespace(http=SimpleNamespace(post=post),
                              client=SimpleNamespace(user_agent="account-specific-ua"))
    assert media.inspect_image(data) == (extension, mime, 12, 20)
    assert media.upload_image(session, data)["width"] == 12
    assert calls[0][1]["files"]["file"] == (f"reply.{extension}", data, mime)
    assert calls[0][1]["headers"]["user-agent"] == "account-specific-ua"
    assert calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("data", [b"", b"not an image", picture("GIF"), picture("JPEG")[:-30]])
def test_invalid_images_rejected(data):
    with pytest.raises(media.MediaError):
        media.inspect_image(data)


def test_image_limits(monkeypatch):
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(media.MediaError, match="10 MiB"):
        media.inspect_image(picture())
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 10_000)
    monkeypatch.setattr(media, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(media.MediaError, match="像素"):
        media.inspect_image(picture())


@pytest.mark.parametrize("raw", [{}, {"object": {"url": "http://bad"}},
                                  {"object": {"url": "https://img.example/x", "pix": "0x2"}},
                                  {"object": {"url": "https://img.example/x", "pix": "broken"}}])
def test_bad_upload_response(raw):
    session = SimpleNamespace(http=SimpleNamespace(post=lambda *a, **k: Response(raw)),
                              client=SimpleNamespace(user_agent="ua"))
    with pytest.raises(media.MediaError):
        media.upload_image(session, picture())


def test_download_uses_message_resource_api_and_stream_limit(monkeypatch):
    calls = []

    class Http(Response):
        def post(self, url, **kwargs):
            return Response({"code": 0, "tenant_access_token": "test-token"})

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response(data=picture())

    monkeypatch.setattr(media, "credentials", lambda _: ("app", "secret", "owner"))
    monkeypatch.setattr(media.NetworkProfile, "http", lambda _: Http())
    assert media.download_image(None, "message/1", "image/key") == picture()
    assert calls[0][0].endswith("/message%2F1/resources/image%2Fkey")
    assert calls[0][1]["params"] == {"type": "image"}
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(media.MediaError, match="10 MiB"):
        media.download_image(None, "message", "image")
