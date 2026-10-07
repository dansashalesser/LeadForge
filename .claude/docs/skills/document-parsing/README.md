# Document Parsing Skill

Local-first document ingestion for PDF, DOCX, XLSX, PPTX, and images using [liteparse](https://github.com/run-llama/liteparse). Invoke before any RAG pipeline or document processing task.

**Skill file:** `~/.claude/skills/document-parsing/SKILL.md`

---

## What It Does

Guides the full document parsing workflow:

1. **Install** — `pip install liteparse` (bundled Tesseract OCR, no cloud deps)
2. **Choose format** — `text` for RAG chunking, `json` for spatial/table extraction, `screenshot` for vision model inputs
3. **CLI patterns** — `lit parse`, `lit batch-parse`, `lit screenshot` with common flags
4. **Python API** — programmatic `liteparse.parse()` / `liteparse.screenshot()` for agentic workflows
5. **RAG handoff** — chunking strategy by output type, feeds into `rag-implementation`
6. **Escalation decision** — when to switch to LlamaParse cloud (handwriting, dense tables, low OCR accuracy)

---

## Supported Formats

| Format | Input | Requires |
|--------|-------|----------|
| PDF | `.pdf` | PDFium (bundled) |
| Office | `.docx`, `.xlsx`, `.pptx`, `.odt`, `.csv` | LibreOffice |
| Images | `.jpg`, `.png`, `.tiff`, `.webp`, `.svg` | ImageMagick |

---

## Activation

This skill fires automatically via the `doc-parse-nudge.sh` hook (UserPromptSubmit) when prompts contain document/parsing/RAG keywords combined with action verbs. It can also be invoked directly:

```
Skill("document-parsing")
```

---

## Related Skills

- `rag-implementation` — downstream consumer of parsed text/JSON
- `rag-architect` — system design for retrieval pipelines
- `pdf-official` — generating (not parsing) PDFs
- `docx-official` — generating (not parsing) Word documents

---

## Source

Extracted from [github.com/run-llama/liteparse](https://github.com/run-llama/liteparse) on 2026-06-08.
Apache 2.0 license.
