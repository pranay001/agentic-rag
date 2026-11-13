"""Arize Phoenix / OpenInference tracing helpers.

`tracer` is usable at import time: until `setup_tracing()` installs the Phoenix provider it is a no-op proxy,
so library code (e.g. retrieval) can always create spans.
"""
import contextvars
import json
import os
import urllib.error
import urllib.request

from openinference.instrumentation import OITracer, TraceConfig
from opentelemetry import trace as trace_api

from . import config

tracer = OITracer(trace_api.get_tracer("datasheet_rag"), config=TraceConfig())
_provider = None


def check_phoenix(endpoint: str = config.PHOENIX_ENDPOINT) -> None:
    """The OTLP exporter drops rejected batches silently, so fail loudly up front instead."""
    req = urllib.request.Request(f"{endpoint}/v1/projects")
    if key := os.getenv("PHOENIX_API_KEY"):
        req.add_header("Authorization", f"Bearer {key}")
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Phoenix has auth enabled: put PHOENIX_API_KEY=<system key> in .env "
                               "(or run Phoenix with PHOENIX_ENABLE_AUTH=false for local use)") from None
        raise
    except urllib.error.URLError as e:
        raise RuntimeError(f"Phoenix not reachable at {endpoint} - run `docker compose up -d` ({e.reason})") from None


def _is_agent_framework_span(span) -> bool:
    scope = getattr(span, "instrumentation_scope", None)
    return bool(scope and scope.name.startswith("agent_framework"))


def setup_tracing(project_name: str = config.PHOENIX_PROJECT, *, trace_images: bool = False,
                  agent_framework: bool = False):
    """Register Phoenix as the global tracer provider and auto-instrument OpenAI or Agent Framework.

    agent_framework=False: OpenAI SDK calls are traced by the OpenInference OpenAI instrumentor.
    agent_framework=True : Agent Framework's own telemetry (agent / chat / execute_tool spans) is converted to
                           OpenInference. The OpenAI instrumentor is NOT enabled then, because it would record every
                           model call a second time (double-counting tokens and cost).

    Idempotent: re-running a notebook cell returns the existing provider.
    """
    global _provider
    if _provider is not None:
        return _provider

    from openinference.instrumentation.openai import OpenAIInstrumentor
    from phoenix.otel import register

    check_phoenix()
    _provider = register(endpoint=f"{config.PHOENIX_ENDPOINT}/v1/traces", project_name=project_name,
                         batch=True, verbose=False)
    if not agent_framework:
        OpenAIInstrumentor().instrument(
            tracer_provider=_provider,
            config=TraceConfig(hide_embedding_vectors=True, hide_input_images=not trace_images,
                               base64_image_max_length=8_000_000),
        )
    else:
        # Agent Framework emits OTel GenAI spans; this processor maps them to OpenInference (agent/LLM/tool kinds).
        # It must run BEFORE the exporter, and Phoenix's add_span_processor() replaces the default exporter,
        # so: converter first (replacing the default), then a fresh Phoenix exporter after it.
        from agent_framework.observability import enable_instrumentation
        from openinference.instrumentation.agent_framework import AgentFrameworkToOpenInferenceProcessor
        from phoenix.otel import BatchSpanProcessor

        enable_instrumentation(enable_sensitive_data=True)
        class _AgentFrameworkOnly(AgentFrameworkToOpenInferenceProcessor):
            # The stock processor rewrites EVERY span (its span_filter only gates forwarding), which would turn our
            # own RETRIEVER/TOOL spans into CHAIN. Convert Agent Framework spans only.
            def on_end(self, span):
                if _is_agent_framework_span(span):
                    super().on_end(span)

        _provider.add_span_processor(_AgentFrameworkOnly(), replace_default_processor=True)
        _provider.add_span_processor(BatchSpanProcessor(endpoint=f"{config.PHOENIX_ENDPOINT}/v1/traces"),
                                     replace_default_processor=False)
    print(f"Tracing to {config.PHOENIX_ENDPOINT}  project='{project_name}'")
    return _provider


def flush() -> None:
    if _provider is not None:
        _provider.force_flush()


def as_json(obj, limit: int = 30_000) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…(truncated)"


def submit_traced(pool, fn, *args, **kwargs):
    """ThreadPoolExecutor does not carry the active span into worker threads; copy the context explicitly."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
