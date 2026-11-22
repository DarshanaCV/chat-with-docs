"""Keep LangChain tool calling while sharing Gemini admission with MCP."""
import asyncio

from langchain_openai import ChatOpenAI

from app.services.gemini_gateway import complete


class QueuedChatOpenAI(ChatOpenAI):
    def _stream(self, *args, **kwargs):
        raise ValueError("Streaming is not implemented for the shared Gemini queue")
        yield  # Keep LangChain's expected generator interface.

    async def _astream(self, *args, **kwargs):
        raise ValueError("Streaming is not implemented for the shared Gemini queue")
        yield

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        # Preserve Gemini thought signatures across tool calls.
        for message, wire in zip(messages, payload["messages"]):
            extras = message.additional_kwargs.get("gemini_tool_extras", {})
            for call in wire.get("tool_calls", []):
                if call["id"] in extras:
                    call["extra_content"] = extras[call["id"]]
        response = complete(payload)
        data = response.model_dump()
        result = self._create_chat_result(data)
        for generation, choice in zip(result.generations, data["choices"]):
            extras = {c["id"]: c["extra_content"] for c in choice["message"].get("tool_calls") or [] if c.get("extra_content")}
            if extras:
                generation.message.additional_kwargs["gemini_tool_extras"] = extras
        return result

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await asyncio.to_thread(
            self._generate, messages, stop=stop, run_manager=run_manager, **kwargs
        )
