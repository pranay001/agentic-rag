"""TruLens LLM-judge provider backed by the OpenAI Responses API.

Why this exists: the `trulens-providers-openai` package requires `openai<2`, which conflicts with Microsoft Agent
Framework (`openai` 3.x). TruLens lets any class that implements `LLMProvider._create_chat_completion` act as a
judge, and every built-in feedback prompt (relevance, groundedness, agent-trace metrics, ...) then works unchanged.
"""
from trulens.core.feedback.endpoint import Endpoint
from trulens.feedback.llm_provider import LLMProvider

from .clients import oai


class ResponsesJudge(LLMProvider):
    """LLM judge using `client.responses` (structured outputs when TruLens asks for a response schema)."""

    reasoning_effort: str = "low"

    def __init__(self, model_engine: str = "gpt-5.4-mini", reasoning_effort: str = "low", rpm: int = 500):
        super().__init__(model_engine=model_engine, reasoning_effort=reasoning_effort,
                         endpoint=Endpoint(name=f"responses-judge-{model_engine}", rpm=rpm))

    def _create_chat_completion(self, prompt=None, messages=None, response_format=None, **kwargs) -> str:
        # TruLens passes `temperature` or `reasoning_effort` in kwargs; GPT-5-family models take only the
        # reasoning effort, so we use the instance setting and ignore those kwargs.
        msgs = list(messages) if messages else [{"role": "system", "content": prompt}]
        args = dict(model=self.model_engine, input=msgs, reasoning={"effort": self.reasoning_effort})
        if response_format is not None:
            r = oai.responses.parse(**args, text_format=response_format)
            return r.output_parsed.model_dump_json() if r.output_parsed is not None else r.output_text
        return oai.responses.create(**args).output_text
