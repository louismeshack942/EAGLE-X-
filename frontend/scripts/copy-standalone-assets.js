// Post-build: ensure the standalone output has .next/static and public/
// so static pages (videos, learn, splash) and media assets work when the
// standalone server is run directly (Render, Docker, local production).
const fs = require("fs");
const path = require("path");

function copyDir(src, dest) {
  if (!fs.existsSync(src)) return 0;
  fs.mkdirSync(dest, { recursive: true });
  let count = 0;
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      count += copyDir(s, d);
    } else {
      fs.copyFileSync(s, d);
      count += 1;
    }
  }
  return count;
}

const root = __dirname + "/..";
const standalone = path.join(root, ".next", "standalone");
if (!fs.existsSync(standalone)) {
  console.log("standalone output not found (output: 'standalone' not enabled) - skipping asset copy");
  process.exit(0);
}

const staticCopied = copyDir(path.join(root, ".next", "static"), path.join(standalone, ".next", "static"));
const publicCopied = copyDir(path.join(root, "public"), path.join(standalone, "public"));
console.log("standalone assets copied: .next/static (" + staticCopied + " files), public (" + publicCopied + " files)");
