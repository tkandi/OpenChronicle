"""Smoke tests for dependencies imported only by real provider response paths."""

from __future__ import annotations


def test_litellm_tool_call_response_path_imports() -> None:
    # LiteLLM imports proxy-oriented helpers lazily when any tools are passed.
    # Unit tests that replace litellm.completion do not exercise that import
    # chain, so pin the exact runtime path used by the classifier.
    import fastapi
    import orjson
    from litellm.responses.mcp.chat_completions_handler import acompletion_with_mcp

    assert fastapi.__version__
    assert orjson.__version__
    assert callable(acompletion_with_mcp)
