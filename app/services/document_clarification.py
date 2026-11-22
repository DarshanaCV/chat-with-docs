"""A saved task plus an explicit suggestion makes confirmation replies meaningful."""
import re
from app.services.document_focus import resolve_focus, ClarifyDocument

STOP = set("the a an of on in for and or to from paper papers pdf document documents file effects explain describe summarize summary figures figure conclusion please this that yes no".split())


def title_matches(question, attached):
    words = set(re.findall(r"[a-z0-9]+", question.casefold())) - STOP
    matches = []
    for doc in attached:
        title = set(re.findall(r"[a-z0-9]+", doc["filename"].casefold())) - STOP
        if words & title:
            matches.append(doc["document_id"])
    return matches


def resolve_turn(question, attached, focus, pending=None):
    q = question.strip().casefold()
    ids = {d["document_id"] for d in attached}
    names = {d["document_id"]: d["filename"] for d in attached}
    cancel = bool(re.search(r"\b(forget that|cancel|never mind|nevermind)\b", q))
    if cancel:
        pending = None
        question = re.sub(r"\b(forget that|cancel|never mind|nevermind)\b[, .!]*", "", question, flags=re.I).strip()
        if not question:
            return {"reply": "Okay, cancelled that request.", "pending": None}
    task = pending["question"] if pending else question

    def ask(message, suggestion=None):
        return {"reply": message, "pending": {"question": task, "suggestion": suggestion}}

    if pending and re.fullmatch(r"(?:yes|yep|yeah|yess+|correct|sure|yes please|that's right)[.!\s]*", q):
        suggestion = pending.get("suggestion")
        if suggestion in ids:
            return {"selected": [suggestion], "question": task, "pending": None}
        return ask("Which PDF do you mean? Please name one; I haven't suggested a single PDF yet.")
    if pending and re.fullmatch(r"(?:no|nope|not that one)[.!\s]*", q):
        return ask("Okay. Which PDF should I use? Please give its name or upload number.")

    new_action = bool(re.search(r"\b(summarize|summarise|explain|describe|compare|list|find)\b", q))
    if pending and new_action:
        task = question
    matches = title_matches(question, attached)
    # 'or' expresses alternatives, never permission to process both.
    if len(matches) > 1 and re.search(r"\bor\b", q):
        return ask("Which one should I use: " + " or ".join(names[i] for i in matches) + "?")
    if len(matches) > 1 and not re.search(r"\b(and|both|all|compare)\b", q):
        return ask("That name matches several PDFs. Which one: " + " or ".join(names[i] for i in matches) + "?")
    if pending and len(matches) == 1 and not new_action:
        suggestion = matches[0]
        return ask(f"Do you mean {names[suggestion]}? Say yes and I'll continue your earlier request, or name another PDF.", suggestion)
    # An explicit new action replaces a pending task; selection-only replies retain it.
    try:
        if len(matches) == 1:
            selected = matches
        else:
            selected = resolve_focus(question, attached, focus)
        if pending and not matches and selected == focus and not new_action:
            # Do not turn an unrelated answer into implicit confirmation.
            if not re.search(r"\b(first|second|third|latest|newest|previous|both|all)\b|\b(?:pdf|document|paper)\s*\d+", q):
                return ask("Please name the PDF you want, or say 'cancel' to stop the earlier request.")
        return {"selected": selected, "question": task, "pending": None}
    except ClarifyDocument as exc:
        return ask(str(exc))
