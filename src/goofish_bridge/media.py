"""单图回复：飞书直连下载，使用目标闲鱼账号上传，不落盘保存截图。"""

from __future__ import annotations

import time
from io import BytesIO
from urllib.parse import quote, urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from goofish_bridge.feishu_adapter import credentials
from goofish_bridge.network import NetworkProfile

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
UPLOAD_URL = "https://stream-upload.goofish.com/api/upload.api"


class MediaError(ValueError):
    """可直接展示给操作人的脱敏媒体错误。"""


def inspect_image(data: bytes) -> tuple[str, str, int, int]:
    """按实际文件内容识别 PNG/JPEG，JPG 和 JPEG 使用同一编码。"""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise MediaError("图片为空或超过 10 MiB，请缩小后重新发送")
    try:
        with Image.open(BytesIO(data)) as picture:
            kind = picture.format
            width, height = picture.size
            if kind not in {"PNG", "JPEG"} or getattr(picture, "n_frames", 1) != 1:
                raise MediaError("仅支持静态 PNG/JPG/JPEG 图片，请以图片方式发送截图")
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise MediaError("图片超过 2500 万像素，请缩小后重新发送")
            picture.verify()
        with Image.open(BytesIO(data)) as picture:
            picture.load()
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise MediaError("图片损坏或格式无法识别，请重新发送 PNG/JPG/JPEG 图片") from None
    return ("png", "image/png", width, height) if kind == "PNG" else (
        "jpg", "image/jpeg", width, height)


def download_image(config, message_id: str, image_key: str) -> bytes:
    """资源只从固定飞书接口获取，下载上限独立于响应头。"""
    app_id, secret, _ = credentials(config)
    try:
        with NetworkProfile().http() as http:
            with http.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                           json={"app_id": app_id, "app_secret": secret},
                           timeout=(10, 20), allow_redirects=False) as response:
                response.raise_for_status()
                raw = response.json()
            token = raw.get("tenant_access_token") if isinstance(raw, dict) else None
            if not isinstance(token, str) or not token or raw.get("code") != 0:
                raise MediaError("飞书图片下载鉴权失败，请检查应用凭据")
            url = ("https://open.feishu.cn/open-apis/im/v1/messages/"
                   f"{quote(message_id, safe='')}/resources/{quote(image_key, safe='')}")
            with http.get(url, params={"type": "image"}, headers={"Authorization": f"Bearer {token}"},
                          stream=True, timeout=(10, 20), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise MediaError("飞书图片下载失败，请检查消息资源下载权限及消息是否仍可访问")
                data = bytearray()
                deadline = time.monotonic() + 60
                for chunk in response.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise MediaError("图片超过 10 MiB，请缩小后重新发送")
                    if time.monotonic() > deadline:
                        raise MediaError("飞书图片下载超时，请稍后重新发送")
                return bytes(data)
    except (requests.RequestException, ValueError) as exc:
        if isinstance(exc, MediaError):
            raise
        raise MediaError("飞书图片下载失败，请检查网络及应用权限") from None


def upload_image(session, data: bytes) -> dict:
    """复用账号的 HTTP Session，继承 Cookie、代理及客户端声明。"""
    extension, mime, width, height = inspect_image(data)
    try:
        with session.http.post(
            UPLOAD_URL, headers={"origin": "https://www.goofish.com",
                                 "referer": "https://www.goofish.com/",
                                 "user-agent": session.client.user_agent},
            params={"floderId": "0", "appkey": "xy_chat", "_input_charset": "utf-8"},
            files={"file": (f"reply.{extension}", data, mime)},
            timeout=(10, 60), allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise MediaError("闲鱼图片上传失败，请检查账号登录状态及网络")
            raw = response.json()
        obj = raw.get("object") if isinstance(raw, dict) else None
        url = obj.get("url") if isinstance(obj, dict) else None
        if not isinstance(url, str) or not url:
            raise MediaError("闲鱼未返回有效图片地址，图片未发送")
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise MediaError("闲鱼返回的图片地址无效，图片未发送")
        # 优先采用服务端实际尺寸，避免上传服务调整图片后出现错误比例。
        if obj.get("pix"):
            width, height = (int(part) for part in str(obj["pix"]).split("x"))
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise MediaError("闲鱼返回的图片尺寸无效，图片未发送")
        return {"url": url, "width": width, "height": height}
    except (requests.RequestException, ValueError) as exc:
        if isinstance(exc, MediaError):
            raise
        raise MediaError("闲鱼图片上传失败或返回内容异常，图片未发送") from None


def prepare_image(config, session, task) -> dict:
    data = download_image(config, task["feishu_reply_message_id"], task["image_key"])
    if time.time() >= task["expires_at"]:
        raise MediaError("图片回复已过期，未上传或发送，请重新引用提交")
    return upload_image(session, data)
