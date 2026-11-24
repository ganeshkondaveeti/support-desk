#!/usr/bin/env node
// Plan P9.11: fail the build if Stitch sample copy, wrong scheme links, raw-HTML
// injection or runtime CDN dependencies reach the UI source or build output.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

// Audit findings S1/S2 (docs/design/stitch/README.md): invented copy and wrong URLs.
const DENY = [
  "LTCG",
  "Regular plan",
  "SEBI Gazette",
  "UPI auto-pay",
  "cryptographic",
  "Cryptographically",
  "leadership-briefing",
  "console.internal",
  "company.internal",
  "Zero hallucination",
  "Audit Throughput",
  "KYC/AML",
  "hdfc-top-100",
  "hdfc-taxsaver",
  "mid-cap-opportunities",
  "hdfc-gold-fund",
  "hdfc-silver-etf-fund-of-fund",
  "Cannot be undone",
];
// Runtime dependencies the audit removed (S4, S6) and HTML injection (P9.4).
const DENY_CODE = ["dangerouslySetInnerHTML", "cdn.tailwindcss.com", "fonts.googleapis.com", "innerHTML"];

const SCAN = [
  { dir: "src", exts: [".ts", ".tsx", ".css"], skip: ["src/api/types.gen.ts", "src/mocks/fixtures"] },
  { dir: "dist", exts: [".js", ".css", ".html"], skip: [] },
  { dir: "index.html", exts: [".html"], skip: [] },
];

function* walk(path) {
  let stat;
  try {
    stat = statSync(path);
  } catch {
    return;
  }
  if (stat.isFile()) {
    yield path;
    return;
  }
  for (const name of readdirSync(path)) yield* walk(join(path, name));
}

const problems = [];
for (const { dir, exts, skip } of SCAN) {
  for (const file of walk(join(root, dir))) {
    const rel = relative(root, file);
    if (!exts.some((e) => rel.endsWith(e))) continue;
    if (skip.some((s) => rel.startsWith(s))) continue;
    if (rel.endsWith("check-content.mjs")) continue;
    // Tests use denylisted strings on purpose, to prove they are rejected.
    if (/\.test\.tsx?$/.test(rel)) continue;
    const text = readFileSync(file, "utf8");
    const isSource = !rel.startsWith("dist");
    for (const word of DENY) if (text.includes(word)) problems.push(`${rel}: Stitch sample text "${word}"`);
    // React's own runtime mentions innerHTML in dist; only source files are held to DENY_CODE fully.
    for (const word of DENY_CODE) {
      if (!isSource && (word === "innerHTML" || word === "dangerouslySetInnerHTML")) continue;
      if (text.includes(word)) problems.push(`${rel}: forbidden "${word}"`);
    }
  }
}

if (problems.length) {
  console.error(`check-content: ${problems.length} problem(s)\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log("check-content: ok");
