from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
import sys
import types

import pytest

if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")

    class _DummyLogger:
        @staticmethod
        def info(*_args, **_kwargs) -> None:
            return None

        @staticmethod
        def warning(*_args, **_kwargs) -> None:
            return None

        @staticmethod
        def error(*_args, **_kwargs) -> None:
            return None

    class _DummyMessageChain:
        def message(self, _text: str):
            return self

    class _DummyStar:
        def __init__(self, context=None):
            self.context = context

    class _DummyStarTools:
        @staticmethod
        def get_data_dir(_name: str) -> str:
            return "."

    class _DummyFilter:
        @staticmethod
        def command(*_args, **_kwargs):
            def _decorator(fn):
                return fn

            return _decorator

    def _register(*_args, **_kwargs):
        def _decorator(cls):
            return cls

        return _decorator

    api_mod.AstrBotConfig = dict
    api_mod.logger = _DummyLogger()
    event_mod.AstrMessageEvent = object
    event_mod.MessageChain = _DummyMessageChain
    event_mod.filter = _DummyFilter()
    star_mod.Context = object
    star_mod.Star = _DummyStar
    star_mod.StarTools = _DummyStarTools
    star_mod.register = _register

    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod

import astrbot_plugin_qfarm.main as main_module
from astrbot_plugin_qfarm.main import QFarmPlugin
from astrbot_plugin_qfarm.services.command_router import RouterReply


class _DummyEvent:
    def plain_result(self, text: str) -> str:
        return f"plain:{text}"

    def image_result(self, image: str) -> str:
        return f"image:{image}"


@pytest.mark.asyncio
async def test_qfarm_entry_fallback_to_text_when_render_payload_is_multi_page(monkeypatch: pytest.MonkeyPatch):
    plugin = QFarmPlugin.__new__(QFarmPlugin)
    plugin.router = SimpleNamespace(handle=AsyncMock(return_value=[RouterReply(text="hello", prefer_image=True)]))
    plugin.image_renderer = SimpleNamespace(render_qfarm=AsyncMock(return_value="/tmp/ok.png"))
    plugin.state_store = SimpleNamespace(get_render_theme=lambda _default="light": "light")

    monkeypatch.setattr(main_module, "should_render_qfarm_image", lambda _text: True)
    monkeypatch.setattr(main_module, "build_qfarm_payload_pages", lambda _text, theme="light": [{"p": 1}, {"p": 2}])

    outputs: list[str] = []
    async for item in plugin.qfarm_entry(_DummyEvent()):
        outputs.append(item)

    assert outputs == ["plain:hello"]
    plugin.image_renderer.render_qfarm.assert_not_awaited()


@pytest.mark.asyncio
async def test_qfarm_entry_renders_single_page_image(monkeypatch: pytest.MonkeyPatch):
    plugin = QFarmPlugin.__new__(QFarmPlugin)
    plugin.router = SimpleNamespace(handle=AsyncMock(return_value=[RouterReply(text="hello", prefer_image=True)]))
    plugin.image_renderer = SimpleNamespace(render_qfarm=AsyncMock(return_value="/tmp/ok.png"))
    plugin.state_store = SimpleNamespace(get_render_theme=lambda _default="light": "light")

    monkeypatch.setattr(main_module, "should_render_qfarm_image", lambda _text: True)
    monkeypatch.setattr(main_module, "build_qfarm_payload_pages", lambda _text, theme="light": [{"p": 1}])

    outputs: list[str] = []
    async for item in plugin.qfarm_entry(_DummyEvent()):
        outputs.append(item)

    assert outputs == ["image:/tmp/ok.png"]
    plugin.image_renderer.render_qfarm.assert_awaited_once()
