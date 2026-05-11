/**
 * Builds usd-simready-inspector-flow.pptx via html2pptx (pptx skill).
 * Env: PPTX_HTML2PPTX_SCRIPTS — directory containing html2pptx.js (default: ~/my-agent-skills/skills/pptx/scripts)
 */
"use strict";

const path = require("path");
const skillScripts =
  process.env.PPTX_HTML2PPTX_SCRIPTS ||
  path.join(process.env.HOME || "", "my-agent-skills/skills/pptx/scripts");
module.paths.unshift(path.join(skillScripts, "node_modules"));

const pptxgen = require("pptxgenjs");
const html2pptx = require(path.join(skillScripts, "html2pptx.js"));

async function main() {
  const dir = __dirname;
  const out = path.join(dir, "..", "usd-simready-inspector-flow.pptx");

  const pptx = new pptxgen();
  pptx.layout = "LAYOUT_16x9";
  pptx.author = "usd-simready-inspector";
  pptx.title = "USD SimReady Inspector — Flow";

  await html2pptx(path.join(dir, "slide01-general.html"), pptx);
  await html2pptx(path.join(dir, "slide02-furniture.html"), pptx);

  await pptx.writeFile({ fileName: out });
  console.log("Wrote", out);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
