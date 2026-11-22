"""Produce model-only copies; leave original tool evidence intact for the UI."""
import json

from langchain_core.messages import ToolMessage


def compact_tool_message(message):
    if not isinstance(message, ToolMessage):
        return message
    def compact(text):
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return text
        if not isinstance(payload, dict) or "sources" not in payload:
            return text
        payload["sources"] = [
            {"source_id": source.get("source_id", f"S{i}"),
             "filename": str(source.get("filename", ""))[:160],
             "page_number": source.get("page_number")}
            for i, source in enumerate(payload["sources"], start=1)
        ]
        return json.dumps(payload, ensure_ascii=False)
    raw = message.content
    if isinstance(raw, str):
        content = compact(raw)
    else:
        content = [dict(block, text=compact(block["text"]))
                   if isinstance(block, dict) and block.get("type") == "text"
                   else block for block in raw]
    return message.model_copy(update={"content": content})


def bound_tool_evidence(messages, budget, measure):
    """Bound model-only tool text; retain message IDs, call pairing and saved originals."""
    tools = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    if not tools or measure(messages) <= budget:
        return messages
    notice = "Evidence omitted to fit context. Do not claim full coverage; request a narrower section if needed."
    result = list(messages)
    # Keep every tool response so tool-call signatures and pairing remain valid.
    for i in tools:
        result[i] = messages[i].model_copy(update={"content": notice})
    remaining = budget - measure(result)
    if remaining <= 0:
        return result
    share = remaining // len(tools)
    seen = set()
    for i in reversed(tools):
        raw = messages[i].content
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        if text in seen:
            continue
        seen.add(text)
        # Serialized escaping is included in the actual budget check below.
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if len(json.dumps(text[:mid]).encode("utf-8")) <= share:
                low = mid
            else:
                high = mid - 1
        result[i] = messages[i].model_copy(update={"content": text[:low] + "\n" + notice})
    while measure(result) > budget:
        longest = max(tools, key=lambda i: len(result[i].content))
        text = result[longest].content
        if len(text) <= len(notice):
            break
        result[longest] = result[longest].model_copy(update={"content": text[:max(0, (len(text)-len(notice))//2)] + notice})
    return result
