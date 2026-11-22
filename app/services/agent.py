import logging
import os
from dotenv import load_dotenv
from app.services.queued_model import QueuedChatOpenAI
from app.services.model_context import compact_tool_message, bound_tool_evidence
from app.services.gemini_gateway import RequestTooLarge, model_id, api_key, BASE_URL, estimate_tokens
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai.chat_models.base import _convert_message_to_dict
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage, HumanMessage
from langgraph.prebuilt import create_react_agent

load_dotenv()


logger = logging.getLogger(__name__)


model = QueuedChatOpenAI(
    model=model_id(),
    base_url=BASE_URL,
    api_key=api_key(),
    temperature=0,
    max_completion_tokens=4096,
    max_retries=0,
    timeout=90,
)

_MAX_HISTORY_TURNS = 3

def _trim_messages(state) -> list[BaseMessage]:
    """Keep recent conversation text and the current turn's tool results."""
    messages = state["messages"] if isinstance(state, dict) else state.messages
    latest_question = next(
        (i for i in range(len(messages) - 1, -1, -1)
         if isinstance(messages[i], HumanMessage)),
        None,
    )
    if latest_question is None:
        return messages

    history = [
        message for message in messages[:latest_question]
        if not isinstance(message, ToolMessage)
        and not (isinstance(message, AIMessage) and message.tool_calls)
    ]
    cleaned = [compact_tool_message(m) for m in history + messages[latest_question:]]
    system_messages = [m for m in cleaned if isinstance(m, SystemMessage)]
    conversation = [m for m in cleaned if not isinstance(m, SystemMessage)]
    question_indices = [
        i for i, message in enumerate(conversation)
        if isinstance(message, HumanMessage)
    ]
    if len(question_indices) <= _MAX_HISTORY_TURNS:
        return cleaned

    cutoff = question_indices[-_MAX_HISTORY_TURNS]
    return system_messages + conversation[cutoff:]


SYSTEM_PROMPT = """
You are Chatterji, a helpful assistant for questions about documents the \
user has uploaded in this chat.

Document tools require a session_id argument. Always pass this exact value, verbatim:
session_id: {session_id}

Use read_document_section first for named sections such as conclusions, methods or results.
Locate sections independently in each selected PDF; never assume identical page ranges.
For read_document_pages with multiple selected PDFs, specify one document_id per call.
If a section lookup is missing or ambiguous, use its heading index or targeted search.
Do not repeat searches that returned the same evidence. If evidence is marked omitted,
state the limitation or request a narrower section rather than claiming complete coverage.
Use search_documents for questions about a specific fact, detail, or topic.
Use get_document_overview for 'summarize', 'overview', or 'what is this document about' \
requests — search_documents alone only returns a few narrowly matching chunks, which is not \
enough for those.

Preserve [S1]-style citations supplied by document tools. Greetings and facts the user
tells you about themselves do not require a document tool. If a tool fails, explain
that the document could not be read or summarized; never invent a document answer.

Use ONLY the backend-selected document IDs for this question. They represent
current conversation focus, which may contain several PDFs for a comparison.
Do not resolve "this paper" from old answers or change files within a question.
Document tools are scoped by the backend. For comparisons, one call executes
against each selected PDF. Never invent evidence from past assistant answers.
Use explain_document_figures for figure explanations, including general requests
like "explain the figures". Omit figure_numbers to cover all indexed figures.
Pass the full user question, not just "Figure". Never guess a sequence of numbers.
Use get_document_index with category="figures" for figure lists, "tables" for table
lists, or "headings" to locate sections. The default overview returns counts only.
Follow each document's own next_offset until null before claiming a complete list.
For paginated index or section follow-ups, pass that document_id; offsets are not shared across PDFs. Do not analyze images
when the user only asks for figure names or page numbers.
Use read_document_pages for original text, references, author lists, and whole sections;
verify section boundaries and follow next_page if more pages remain. Do not summarize
pages when the user wants a complete list. Search returns selected passages, not all items.
Figure tools return coverage reports. Explain successful items and explicitly list
unavailable ones. Do not claim complete PDF coverage from partial evidence.
Pass filename or document_id when the user specifies another document; use
all_documents=true only for explicit requests covering all documents. For page
requests, call read_document_pages for original text or get_document_overview for summaries
with page_start/page_end (1-based, inclusive),
even if an earlier whole-document summary failed. Do not invent a page summary.

If no documents are attached, or a tool reports none found, answer from your own knowledge \
and say so plainly."""

def build_agent(tools, checkpointer):
    from app.services.document_focus import scoped_tools
    tools = scoped_tools(tools)
    schemas = [convert_to_openai_tool(tool) for tool in tools]

    def prepare_context(state):
        messages = _trim_messages(state)
        limit = int(os.getenv("GEMINI_CHAT_INPUT_BUDGET", "60000"))
        while estimate_tokens({"messages": [_convert_message_to_dict(m) for m in messages],
                              "tools": schemas}) > limit:
            questions = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
            if len(questions) <= 1:
                messages = bound_tool_evidence(messages, limit, lambda items: estimate_tokens(
                    {"messages": [_convert_message_to_dict(m) for m in items], "tools": schemas}))
                if estimate_tokens({"messages": [_convert_message_to_dict(m) for m in messages], "tools": schemas}) > limit:
                    raise RequestTooLarge("The question itself exceeds the input budget. Please shorten it.")
                break
            messages = [m for m in messages[:questions[1]] if isinstance(m, SystemMessage)] + messages[questions[1]:]
        return messages

    # LangGraph validates tool-call pairing before applying a prompt callback.
    # Clean older failed turns before that validation, without deleting saved history.
    def prepare_model_input(state):
        return {"llm_input_messages": prepare_context(state)}

    return create_react_agent(model, tools, checkpointer=checkpointer,
                             pre_model_hook=prepare_model_input)
