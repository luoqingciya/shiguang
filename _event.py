"""事件适配层 — 把 Qingci MatcherContext 适配为原插件事件接口

原 getpx 插件（画境拾珍）的 Mixin 代码深度依赖 AstrBot 的
AstrMessageEvent / Plain / Image / Node / Nodes 接口。本模块提供等价
轻量实现（组件为简单容器），使业务层代码在移植时几乎零改动：

- EventAdapter: 封装 Qingci MatcherContext + 插件实例，提供原插件用到的
  event 接口子集（get_sender_id / get_group_id / get_platform_name /
  plain_result / chain_result / send 等）
- Plain / Image / File / Node / Nodes: 消息组件容器，chain_result 转换时
  识别为 v12 消息段发送

发送层：
- 文本 / 普通图片链 → connection.send_msg（v12 段数组）
- Nodes（合并转发）→ 仅 OneBot 11（平台名 "onebot"）走 call_api
  send_forward_msg，其余平台自动降级逐条发送
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

ONE_BOT_11_PLATFORM = "onebot"
MAX_FORWARD_RETRIES = 3


# ──────────────────────────────────────────────────────────────────────
# 消息组件（轻量容器）
# ──────────────────────────────────────────────────────────────────────


@dataclass
class Plain:
    text: str

    def __str__(self) -> str:
        return self.text


@dataclass
class Image:
    path: str = ""
    file_id: str = ""
    url: str = ""
    base64: str = ""
    file: str = ""

    @classmethod
    def fromFileSystem(cls, path: str) -> Image:
        return cls(path=str(path))


@dataclass
class File:
    name: str = ""
    file: str = ""


@dataclass
class Node:
    uin: int | str = 0
    name: str = ""
    content: list[Any] = field(default_factory=list)


@dataclass
class Nodes:
    nodes: list[Node] = field(default_factory=list)


# plain_result / chain_result 的返回包装
@dataclass
class _Result:
    kind: str  # "text" | "chain"
    value: Any


# ──────────────────────────────────────────────────────────────────────
# 事件适配器
# ──────────────────────────────────────────────────────────────────────


class EventAdapter:
    """把 Qingci MatcherContext 包装为原插件事件接口"""

    def __init__(self, ctx: Any, plugin: Any):
        self._ctx = ctx
        self._plugin = plugin  # 插件实例（含 connection / config / data_dir）

    # ---- 上下文读取 ----

    def get_sender_id(self) -> str:
        return str(self._ctx.user_id or "")

    def get_group_id(self) -> str:
        return str(self._ctx.group_id or "")

    def get_platform_name(self) -> str:
        return str(getattr(self._ctx, "platform", "") or "onebot")

    def get_self_id(self) -> str:
        return str(self._ctx.self_id or "")

    def get_sender_name(self) -> str:
        return str(self._ctx.sender_name or "")

    def is_group(self) -> bool:
        return bool(self.get_group_id())

    def is_private(self) -> bool:
        return not self.is_group()

    def stop_event(self) -> None:
        """原插件用 event.stop_event() 阻止后续处理器；Qingci 匹配器
        block=True 语义一致，无需额外处理。"""
        return None

    # ---- 结果构造 ----

    def plain_result(self, text: str) -> _Result:
        return _Result(kind="text", value=str(text))

    def chain_result(self, content: list[Any]) -> _Result:
        return _Result(kind="chain", value=content)

    # ---- 主动发送 ----

    async def send(self, result: _Result) -> None:
        if result.kind == "text":
            await self._send_text(str(result.value))
            return
        if result.kind == "chain":
            await self._send_chain(result.value)
            return
        raise TypeError(f"不支持的发送结果: {result!r}")

    async def _send_text(self, text: str) -> None:
        if not text:
            return
        await self._send_segments([{"type": "text", "data": {"text": text}}])

    async def _send_chain(self, content: list[Any]) -> None:
        if not content:
            return
        # 合并转发：Nodes 列表（仅 OneBot 11 支持）
        if len(content) == 1 and isinstance(content[0], Nodes):
            if self.get_platform_name() == ONE_BOT_11_PLATFORM:
                await self._send_forward(content[0])
            else:
                for node in content[0].nodes:
                    await self._send_segments(_components_to_segments(node.content))
            return
        await self._send_segments(_components_to_segments(content))

    async def _send_segments(self, segments: list[dict]) -> None:
        connection = self._connection()
        if connection is None:
            raise RuntimeError("插件连接未就绪，无法发送消息")
        message_type = "group" if self.is_group() else "private"
        target = int(self.get_group_id() or self.get_sender_id() or 0)
        await connection.send_msg(message_type, target, segments)

    async def _send_forward(self, nodes: Nodes) -> None:
        connection = self._connection()
        if connection is None:
            raise RuntimeError("插件连接未就绪，无法发送消息")
        group_id = int(self.get_group_id() or 0)
        if not group_id:
            raise RuntimeError("合并转发仅支持群聊")
        messages = [
            {
                "type": "node",
                "data": {
                    "uin": int(node.uin or 0),
                    "name": str(node.name or ""),
                    "content": _components_to_segments(node.content),
                },
            }
            for node in nodes.nodes
        ]
        last_error: Exception | None = None
        for attempt in range(1, MAX_FORWARD_RETRIES + 1):
            try:
                await connection.call_api(
                    "send_forward_msg", {"group_id": group_id, "messages": messages}
                )
                return
            except Exception as e:  # noqa: BLE001 - 重试后降级
                last_error = e
                if attempt < MAX_FORWARD_RETRIES:
                    await asyncio.sleep(attempt * 2)
        raise last_error if last_error else RuntimeError("合并转发失败")

    def _connection(self) -> Any:
        plugin = self._plugin
        connection = getattr(plugin, "connection", None)
        if connection is not None:
            return connection
        bot = getattr(plugin, "bot", None)
        return getattr(bot, "connection", None) if bot is not None else None


# ──────────────────────────────────────────────────────────────────────
# 组件 → v12 消息段
# ──────────────────────────────────────────────────────────────────────


def _components_to_segments(content: list[Any]) -> list[dict]:
    segments: list[dict] = []
    for component in content:
        if isinstance(component, Plain):
            segments.append({"type": "text", "data": {"text": str(component.text)}})
        elif isinstance(component, Image):
            if component.file_id:
                segments.append({"type": "image", "data": {"file_id": component.file_id}})
            elif component.base64:
                segments.append({"type": "image", "data": {"file": component.base64}})
            elif component.url:
                segments.append({"type": "image", "data": {"file": component.url}})
            else:
                segments.append({"type": "image", "data": {"file": component.path}})
        elif isinstance(component, File):
            segments.append(
                {"type": "file", "data": {"file": str(component.file or component.name)}}
            )
        elif isinstance(component, str):
            segments.append({"type": "text", "data": {"text": component}})
        else:
            segments.append({"type": "text", "data": {"text": str(component)}})
    return segments


def result_to_reply(result: _Result) -> str | list[dict] | None:
    """入口 handler 把 Mixin yield 的结果转为 Qingci 返回值：
    - text → 文本
    - chain → v12 段数组（图片链）
    """
    if result is None:
        return None
    if result.kind == "text":
        return str(result.value)
    if result.kind == "chain":
        return _components_to_segments(result.value)
    return None
