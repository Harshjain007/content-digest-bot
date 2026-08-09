"""Prompt construction with LangChain FewShotPromptTemplate.

We use LangChain's prompt templating (not its model integrations) so the bot
keeps calling Ollama / Anthropic directly. Few-shot examples lock the model
into a consistent, well-structured output format for both the short summary
and the full deep-dive.
"""
from langchain_core.prompts import (
    FewShotPromptTemplate,
    PromptTemplate,
)

# ------------------------------------------------------------------- Summary
SUMMARY_EXAMPLES = [
    {
        "input": "what is Retrieval-Augmented Generation",
        "output": (
            "- RAG combines a search/retrieval step with a generative LLM so answers "
            "are grounded in real documents, not just model memory.\n"
            "- It matters because it cuts hallucination and lets the model use "
            "up-to-date or private data.\n"
            "- You can build QA bots over your docs, customer-support assistants, "
            "and research tools that cite sources.\n"
            "- It's cheaper than fine-tuning and easy to update — just change the data.\n"
            "- Widely used in support, legal, and internal knowledge search."
        ),
    },
    {
        "input": "explain vector databases",
        "output": (
            "- A vector database stores data as embeddings (lists of numbers) and "
            "finds items by similarity, not exact match.\n"
            "- It matters for semantic search, recommendations, and AI memory.\n"
            "- You can power semantic search, deduplication, and RAG retrieval.\n"
            "- Popular engines: FAISS, Pinecone, Qdrant, Chroma, pgvector.\n"
            "- Trade-off: approximate search is fast but can miss exact matches."
        ),
    },
]

SUMMARY_PREFIX = (
    "You are an expert explainer. Given a TOPIC or extracted SOURCE CONTENT, "
    "write a SHORT summary. Rules:\n"
    "- 4 to 6 bullet points, each starting with '- '.\n"
    "- Cover: what it is in one line, why it matters, and 2-3 concrete things "
    "you can do with it.\n"
    "- Plain language, no headers, no markdown bold, no sections.\n"
    "- Under 150 words.\n"
    "- If the source content failed to extract, say so in one line then summarize "
    "what it likely is."
)

SUMMARY_EXAMPLE_PROMPT = PromptTemplate(
    input_variables=["input", "output"],
    template="TOPIC / CONTENT:\n{input}\n\nSUMMARY:\n{output}",
)

summary_prompt = FewShotPromptTemplate(
    examples=SUMMARY_EXAMPLES,
    example_prompt=SUMMARY_EXAMPLE_PROMPT,
    prefix=SUMMARY_PREFIX,
    suffix="TOPIC / CONTENT:\n{input}\n\nSUMMARY:",
    input_variables=["input"],
)

# ---------------------------------------------------------------------- Full
FULL_EXAMPLES = [
    {
        "input": "what is Retrieval-Augmented Generation",
        "output": (
            "## TL;DR\n"
            "- RAG grounds an LLM in retrieved documents for factual answers.\n"
            "- It reduces hallucination and enables private/up-to-date data.\n\n"
            "## What it is\n"
            "RAG is a pattern that retrieves relevant text from a knowledge source "
            "and feeds it to a generative model as context.\n\n"
            "## How it works\n"
            "1. Chunk and embed your documents into a vector store.\n"
            "2. On a query, embed it and retrieve the top-k nearest chunks.\n"
            "3. Stuff those chunks into the prompt and let the LLM answer.\n\n"
            "## What you can do with it\n"
            "- Realistic: doc QA, support bots, legal research.\n"
            "- Speculative: agentic long-term memory.\n\n"
            "## Key details & nuances\n"
            "Retrieval quality dominates results; bad chunks = bad answers.\n\n"
            "## Notable examples / tools\n"
            "LangChain, LlamaIndex, FAISS, Pinecone.\n\n"
            "## How to go deeper\n"
            "Read the LlamaIndex docs; build a small PDF QA bot.\n\n"
            "## Open questions\n"
            "How to evaluate retrieval quality automatically?"
        ),
    },
]

FULL_PREFIX = (
    "You are an expert explainer and analyst. Given a TOPIC or extracted SOURCE "
    "CONTENT, produce a structured deep-dive in Markdown. Always use these "
    "sections, in order:\n"
    "## TL;DR  (3-5 bullets)\n"
    "## What it is\n"
    "## How it works  (numbered steps or bullets)\n"
    "## What you can do with it  (split Realistic vs Speculative)\n"
    "## Key details & nuances  (limits, costs, gotchas)\n"
    "## Notable examples / tools / players\n"
    "## How to go deeper  (3-5 concrete next steps)\n"
    "## Open questions\n"
    "Rules: be specific and concrete; if content is thin, reason from first "
    "principles and mark what is inferred; keep it scannable; 400-900 words."
)

FULL_EXAMPLE_PROMPT = PromptTemplate(
    input_variables=["input", "output"],
    template="TOPIC / CONTENT:\n{input}\n\nDEEP-DIVE:\n{output}",
)

full_prompt = FewShotPromptTemplate(
    examples=FULL_EXAMPLES,
    example_prompt=FULL_EXAMPLE_PROMPT,
    prefix=FULL_PREFIX,
    suffix="TOPIC / CONTENT:\n{input}\n\nDEEP-DIVE:",
    input_variables=["input"],
)


def build_prompt_text(data, user_note=None, mode="full"):
    """Return the final prompt string for the given mode."""
    src = data.get("source")
    title = data.get("title", "")
    url = data.get("url")
    text = data.get("text") or ""

    if src == "Topic":
        block = f"TOPIC (explain from knowledge): {title}"
    else:
        block = f"SOURCE TYPE: {src}\nTITLE: {title}"
        if url:
            block += f"\nURL: {url}"
        if text:
            block += f"\n\nEXTRACTED CONTENT:\n{text}"
        else:
            block += ("\n\nNOTE: extraction failed — explain what this likely is "
                      "and how to learn more.")

    if user_note:
        block += f"\n\nUSER'S ANGLE / QUESTION: {user_note}"

    tmpl = summary_prompt if mode == "summary" else full_prompt
    return tmpl.format(input=block)


# ======================================================= JSON KNOWLEDGE MODES
TOOL_JSON_SCHEMA = """Return ONLY a JSON object (no markdown fences) with these keys:
{
  "title": "name of the tool/repo",
  "description": "summary of what the tool does, combining the article summary and the GitHub README if both are present",
  "problem": "what problem this solves and how it makes life easier",
  "how this works": "step-by-step guide on how to use / set up this tool",
  "links": {"github": "<github url or empty string>", "website": "<website/homepage or empty string>", "article": "<original article url or empty string>"}
}"""

LEARNING_JSON_SCHEMA = """Return ONLY a JSON object (no markdown fences) with these keys:
{
  "description": "what problem this solves or what the article is about",
  "links": "<link to the article>",
  "takeAways": "the concrete things learned / actionable advice from the article"
}"""


def build_tool_json_prompt(article_text, github_text, article_url, github_url,
                           website):
    parts = ["You are a knowledge-keeper bot. Build a structured JSON record for "
             "a tool/repository the user shared.\n"]
    if article_text:
        parts.append("ARTICLE TEXT:\n" + article_text[:12000])
    if github_text:
        parts.append("GITHUB README:\n" + github_text[:16000])
    if not article_text and not github_text:
        parts.append("NOTE: no extracted text available; describe from the link "
                     "metadata if possible, else say so.")
    parts.append(f"Article URL: {article_url or ''}")
    parts.append(f"GitHub URL: {github_url or ''}")
    parts.append(f"Website: {website or ''}")
    parts.append("\n" + TOOL_JSON_SCHEMA)
    return "\n\n".join(parts)


def build_learning_json_prompt(article_text, article_url):
    parts = ["You are a knowledge-keeper bot. Build a structured JSON record for "
             "an upskilling / productivity / self-improvement article.\n"]
    if article_text:
        parts.append("ARTICLE TEXT:\n" + article_text[:16000])
    else:
        parts.append("NOTE: no extracted text; summarize from the title/URL.")
    parts.append(f"Article URL: {article_url or ''}")
    parts.append("\n" + LEARNING_JSON_SCHEMA)
    return "\n\n".join(parts)
