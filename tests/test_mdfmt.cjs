/* Guard the markdown renderer's XSS defences.
 *
 * mdfmt() turns model-written markdown into HTML, so it is the one place a
 * crafted entry could smuggle a tag through. The invariant: every tag in the
 * output is one mdfmt generated — input `<` is always escaped first — and
 * every href is http(s). Runs the real site/mdfmt.js and the real safeUrl()
 * lifted out of the template, so it cannot drift from what ships.
 *
 * Run:  node tests/test_mdfmt.cjs
 */
const fs = require("fs"); const vm = require("vm");
// Load the two functions that turn untrusted model text into HTML.
const mdfmtSrc = fs.readFileSync("site/mdfmt.js", "utf8");
const page = fs.readFileSync("site/template.html", "utf8");
const safeUrlSrc = page.match(/function safeUrl[\s\S]*?\n}/)[0];
const linkHTMLSrc = page.match(/function linkHTML[\s\S]*?\n}/)[0];
const ESCSrc = page.match(/const ESC = \{.*?\};/s)[0];
vm.runInThisContext(mdfmtSrc + "\n" + ESCSrc + "\n" + safeUrlSrc + "\n" + linkHTMLSrc);

const payloads = [
  `<img src=x onerror=alert(1)>`,
  `<script>alert(1)</script>`,
  `"><svg onload=alert(1)>`,
  `**bold** \`<b>raw</b>\``,
  "@url:`click`<javascript:alert(1)>",
  "@url:`javascript:alert(1)`",
  `visit javascript:alert(1) now`,
  `- item <iframe src=javascript:alert(1)>`,
  `1. one 2. two <a href="javascript:alert(1)">x</a>`,
  `https://ok.example.com/p?a=1&b=2`,
  `'"--></style></script><script>alert(1)</script>`,
];
let fail = 0;
for (const p of payloads) {
  const out = mdfmt(p);
  const bad = [];
  // Every tag in the output must be one mdfmt generated. Input `<` is escaped,
  // so any tag here came from the formatter, not the payload.
  const ALLOWED = /^\/?(p|ul|ol|li|h3|h4|code|strong|em|a)$/;
  for (const m of out.matchAll(/<([^>]*)>/g)) {
    const t = m[1].trim();
    if (ALLOWED.test(t)) continue;
    if (/^a href="https?:\/\/[^"]*" target="_blank" rel="noopener noreferrer"$/.test(t)) continue;
    bad.push("UNEXPECTED_TAG<" + t + ">");
  }
  if (bad.length) { fail++; console.log("  FAIL", JSON.stringify(p), "->", bad.join(","), "\n      ", out); }
  else console.log("  ok   ", JSON.stringify(p.slice(0, 46)));
}
// hrefs must only ever be http(s)
for (const u of ["javascript:alert(1)", "data:text/html,<script>", "vbscript:x",
                 "  javascript:alert(1)", "https://ok.example.com/x"]) {
  const out = linkHTML(u, "L");
  const ok = u.startsWith("https") ? out.includes('href="https://ok.example.com/x"') : out === "";
  if (!ok) { fail++; console.log("  FAIL linkHTML", u, "->", out); }
  else console.log("  ok    linkHTML", JSON.stringify(u.slice(0, 30)));
}
console.log(fail ? `\nRESULT: FAIL (${fail})` : "\nRESULT: PASS");
process.exit(fail ? 1 : 0);
