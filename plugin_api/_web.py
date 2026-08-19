"""Quart 兼容 shim — 把 FastAPI 请求适配为 plugin_api 期望的 quart 风格接口

原插件 plugin_api/api.py 直接使用 quart 的全局 `request` / `jsonify` /
`send_file`。Qingci 插件 Web API 的 handler 接收 FastAPI Request 参数。
本模块提供等价物：

- `request`: 代理当前协程的 FastAPI Request（contextvars 隔离）
- `jsonify`: 返回 dict（框架按 (data, status) 契约序列化）
- `send_file`: 返回 FastAPI FileResponse
- UploadFile 适配为 quart FileStorage 风格（filename/stream/content_length）
"""

from __future__ import annotations

import contextvars
from typing import Any

from fastapi.responses import FileResponse

_current_request: contextvars.ContextVar[Any] = contextvars.ContextVar("shiguang_web_request")


class _UploadStream:
    def __init__(self, upload: Any):
        self._upload = upload

    def read(self, size: int = -1) -> bytes:
        return bytes(self._upload.file.read(size))


class UploadAdapter:
    """FastAPI UploadFile → quart FileStorage 风格"""

    def __init__(self, upload: Any):
        self._upload = upload
        self.filename: str = str(getattr(upload, "filename", "") or "")
        self.stream = _UploadStream(upload)

    @property
    def content_length(self) -> int | None:
        upload = self._upload
        size = getattr(upload, "size", None)
        if size is None:
            return None
        try:
            return int(size)
        except (TypeError, ValueError):
            return None


class _Files:
    """`await request.files` → FastAPI FormData（支持 keys()/getlist()）"""

    def __init__(self, request: Any):
        self._request = request

    async def _load(self):
        form = await self._request.form()
        # 把每个 UploadFile 适配为 quart FileStorage 风格
        files = type("Form", (), {})()
        files.keys = lambda: list(form.keys())
        files.getlist = lambda key: [UploadAdapter(u) for u in form.getlist(key)]
        return files

    def __await__(self):
        return self._load().__await__()


class _RequestProxy:
    """全局 `request` 访问（当前协程的 FastAPI Request）"""

    @property
    def args(self) -> Any:
        return _current_request.get().query_params

    async def get_json(self, silent: bool = False) -> Any:
        try:
            return await _current_request.get().json()
        except Exception:
            if silent:
                return None
            raise

    async def get_data(self) -> bytes:
        return bytes(await _current_request.get().body())

    @property
    def files(self) -> _Files:
        return _Files(_current_request.get())

    @property
    def json(self) -> Any:
        return getattr(_current_request.get(), "json", None)


request = _RequestProxy()


def jsonify(data: Any, status: int = 200):
    """返回 (data, status)；status=200 时仅返回 data（框架按默认 200 处理）"""
    if status == 200:
        return data
    return (data, status)


def send_file(
    path: str,
    mimetype: str | None = None,
    as_attachment: bool = False,
    attachment_filename: str | None = None,
    **kwargs: Any,
) -> FileResponse:
    """FastAPI FileResponse；attachment_filename → filename"""
    return FileResponse(
        path,
        media_type=mimetype,
        filename=attachment_filename if as_attachment else None,
        **kwargs,
    )


def wrap_handler(handler):
    """把无参 handler 包装为接收 FastAPI Request 的契约"""

    async def wrapped(request):
        token = _current_request.set(request)
        try:
            return await handler()
        finally:
            _current_request.reset(token)

    return wrapped
