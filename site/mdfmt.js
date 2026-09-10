/* mdfmt — render the markdown an LLM writes into readable, safe HTML.
 *
 * Entries are model output, so the order here is load-bearing: escape the
 * whole string FIRST, then add markup. Nothing from the source can become a
 * tag, and the only hrefs produced come from safeUrl() in the page.
 *
 * Handles what the digest prompts actually emit: ## headings, - and 1.
 * lists, **bold**, *italic*, `code`, blank-line paragraphs, and the
 * @url:`label`<href> form the extractor writes for links.
 */
const MD_ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => MD_ESC[c]);
}

/* Inline markup, applied to already-escaped text. */
function mdInline(t) {
  // @url:`label`<href> and @url:`href` — written by the extractor.
  t = t.replace(/@url:`([^`]+)`&lt;([^&]+?)&gt;/g,
    (_, label, url) => mdLink(url, label));
  t = t.replace(/@url:`([^`]+)`/g, (_, url) => mdLink(url, url));
  // Bare URLs left in prose by the model.
  t = t.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (m, pre, url) => pre + mdLink(url.replace(/[.,;:]+$/, ""), null));
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return t;
}

/* Build one link, dropping anything that is not http(s). Mirrors the page's
   safeUrl() so mdfmt is safe on its own if reused elsewhere. */
function mdLink(raw, label) {
  let parsed;
  try { parsed = new URL(String(raw).replace(/&amp;/g, "&")); } catch { return esc(label || raw); }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return esc(label || raw);
  const text = label || parsed.hostname.replace(/^www\./, "") + parsed.pathname.replace(/\/$/, "");
  return `<a href="${esc(parsed.href)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
}

function mdfmt(s) {
  // Models often write a whole numbered sequence on one line
  // ("1. Install ... 2. Configure ... 3. Run ..."). Break those apart so they
  // read as the steps they are, not as one wall of text. Only lines that
  // already start as a step are split, so prose containing "4. " is safe.
  const src = esc(s).replace(/\r\n?/g, "\n").split("\n").map(line => {
    const l = line.trim();
    if (!/^\d{1,2}[.)]\s/.test(l)) return line;
    return l.replace(/\s+(?=\d{1,2}[.)]\s)/g, "\n");
  }).join("\n");
  const out = [];
  let list = null;          // "ul" | "ol" | null
  let para = [];

  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const closePara = () => {
    if (para.length) { out.push(`<p>${mdInline(para.join(" "))}</p>`); para = []; }
  };

  for (const rawLine of src.split("\n")) {
    const line = rawLine.trim();

    if (!line) { closePara(); closeList(); continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closePara(); closeList();
      // Depth is relative: a note's own "# Title" is a section here, not a
      // page title, so everything shifts down to h3/h4.
      const tag = heading[1].length <= 2 ? "h3" : "h4";
      out.push(`<${tag}>${mdInline(heading[2].replace(/[:#]+$/, ""))}</${tag}>`);
      continue;
    }

    const bullet = line.match(/^[-*•]\s+(.*)$/);
    if (bullet) {
      closePara();
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${mdInline(bullet[1])}</li>`);
      continue;
    }

    const numbered = line.match(/^(\d+)[.)]\s+(.*)$/);
    if (numbered) {
      closePara();
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${mdInline(numbered[2])}</li>`);
      continue;
    }

    // A continuation line inside a list item belongs to that item.
    if (list) { out.push(out.pop().replace(/<\/li>$/, " " + mdInline(line) + "</li>")); continue; }

    para.push(line);
  }
  closePara();
  closeList();
  return out.join("");
}

/* Plain text, for previews and search. */
function mdText(s) {
  return String(s == null ? "" : s)
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/@url:`([^`]+)`(<[^>]+>)?/g, "$1")
    .replace(/[*`>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
