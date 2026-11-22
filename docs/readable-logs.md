# Reading the terminal log

Each line shows the time, a short chat ID and the current action. Warnings and
errors retain their level. A separator starts each question. Multiple chats can
interleave, so use the chat ID to follow one conversation.

Document tool start/end messages come from MCP. Nested API callbacks are kept
at DEBUG level in the file, avoiding duplicate terminal progress. Gemini reviewing
a tool result may request another search; it is not necessarily writing the final
answer yet. Long operations still report progress every 15 seconds.

Searches show their query, returned page numbers and indented text previews.
Full text for every candidate, reranked and returned passage remains in the
SEARCH_TEXT entries in logs/mcp-<pid>.log. The startup message gives the exact
log path. Console previews are shortened; use the file for complete evidence.
Model IDs and detailed token usage also remain in the DEBUG file.

## The supplied 14:22:10 question

Question: summarize the Physical AI paper and list all authors.
Total time: 38.1 seconds.

- First preparation: 25 pages, 128 passages, 11 caption locations. Embedding
  generation took 12.2 seconds, total preparation 12.7 seconds.
- Full-document Gemini summary: 5.6 seconds.
- Search for "authors": returned reference-list passages on page 18.
- Agent requested a separate page-one summary: Gemini took 2.6 seconds.
- Search for "authors by line or et al": returned pages 16, 18 and 19.
- Search for the paper title: finally returned page 1, including the byline.
- Final answer generated; 25 sources attached. This count does not prove that
  every source was useful or that the final author list was complete.

There was one embedding pass, two different summary requests and three actual
searches. Repeated callback messages were not additional executions. No image
analysis ran. The original "Found 0 figures" line was misleading because that
stage was skipped, while 11 caption locations had been indexed.

The author lookup detour remains a retrieval issue, separate from log formatting.
