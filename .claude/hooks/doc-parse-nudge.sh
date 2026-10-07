#!/usr/bin/env bash
# doc-parse-nudge.sh — UserPromptSubmit hook
#
# Detects when the user is about to parse documents or build RAG pipelines
# and injects a reminder to invoke the document-parsing skill first.
# Fires on every user prompt; exits immediately if no keywords match (<5ms typical).
#
# REGISTRATION (in settings.json under "UserPromptSubmit"):
# {
#   "matcher": "",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/doc-parse-nudge.sh", "timeout": 5}]
# }

set -euo pipefail

PROMPT_TEXT=$(python3 - 2>/dev/null <<'PYEOF'
import json, sys
try:
    d = json.load(sys.stdin)
    text = d.get("prompt", "") or ""
    print(text.lower())
except Exception:
    pass
PYEOF
) || PROMPT_TEXT=""

[ -z "$PROMPT_TEXT" ] && exit 0

# Document / parsing keywords
DOC_KEYWORDS="pdf|docx|pptx|xlsx|\.doc |\.pdf |document|parse|parsing|extract text|text extract|ocr |liteparse|llamaparse|unstructured|pypdf|pdfplumber|pymupdf"

# RAG / ingestion keywords
RAG_KEYWORDS="rag |retrieval.augmented|vector store|knowledge base|embed |embedding|ingest |ingestion|chunk |chunking|index document|load document|document loader|vector db|pinecone|weaviate|chroma|qdrant|faiss"

# Action verbs — only nudge when building, not when asking questions
ACTION_KEYWORDS="build|create|set up|implement|add |write|design|scaffold|integrate|make a|develop"

has_doc=false
has_rag=false
has_action=false

echo "$PROMPT_TEXT" | grep -qiE "$DOC_KEYWORDS"   && has_doc=true
echo "$PROMPT_TEXT" | grep -qiE "$RAG_KEYWORDS"    && has_rag=true
echo "$PROMPT_TEXT" | grep -qiE "$ACTION_KEYWORDS" && has_action=true

# Nudge when: (building something AND working with documents) OR (building RAG pipeline)
if { [ "$has_action" = true ] && [ "$has_doc" = true ]; } || \
   { [ "$has_action" = true ] && [ "$has_rag" = true ]; }; then
  cat << 'NUDGE'
╔══ Document Parsing Nudge ══════════════════════════════════════════╗
║  Document/RAG work detected.                                       ║
║  Before building: invoke the document-parsing skill for local      ║
║  PDF/DOCX/image ingestion, format selection (text vs JSON+bbox),   ║
║  OCR config, and RAG handoff patterns using liteparse.             ║
║  → Skill("document-parsing")                                       ║
╚════════════════════════════════════════════════════════════════════╝
NUDGE
fi

exit 0
