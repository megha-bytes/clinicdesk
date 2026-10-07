"""A fake OpenAI-compatible client for router and graph tests (no network, no key)."""
from __future__ import annotations

import json
from types import SimpleNamespace


def msg(content=None, tool_calls=None, prompt_tokens=100, completion_tokens=20):
    calls = [SimpleNamespace(id=f"call_{i}", type="function",
                             function=SimpleNamespace(name=name, arguments=json.dumps(args)))
             for i, (name, args) in enumerate(tool_calls or [])]
    message = SimpleNamespace(content=content, tool_calls=calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))


class FakeClient:
    """`handler(kwargs) -> response` decides each reply; every request is kept in `.requests`."""

    def __init__(self, handler):
        self.requests: list[dict] = []
        self.handler = handler
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.handler(kwargs)
