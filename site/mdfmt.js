function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ESC[c]);
}

/* Convert common LLM markdown into safe HTML, then escape.
   Handles: **bold**, ## heading, numbered lists, @url:`text` links. */
function mdfmt(s) {
  let t = esc(s);
  t = t.replace(/@url:`([^`]+)`<([^>]+)>/g,
    (_, label, url) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`);
  t = t.replace(/@url:`([^`]+)`/g,
    (_, label) => `<a href="${esc(label)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`);
  t = t.replace(/(?:^|\n)(\d+)\.\s(.+?)(?=\n\d+\.\s|$)/g, (_, n, txt) => `<li>${txt}</li>`);
  t = t.replace(/(?:^|\n)(<li>.*<\/li>\n?)+/g, m => `<ol>${m}</ol>`);
  t = t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  t = t.replace(/^## (.+)$/gm, "<h3>$1</h3>");
  return t;
}