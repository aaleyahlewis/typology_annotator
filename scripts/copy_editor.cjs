// Copies the Label Studio editor build into app/static/lsf (served by the app, not a CDN).
const fs = require("fs");
const path = require("path");
const src = path.join(__dirname, "..", "node_modules", "label-studio-frontend", "dist", "lsf");
const dest = path.join(__dirname, "..", "app", "static", "lsf");
fs.rmSync(dest, { recursive: true, force: true });
fs.mkdirSync(path.join(dest, "js"), { recursive: true });
fs.mkdirSync(path.join(dest, "css"), { recursive: true });
for (const f of fs.readdirSync(path.join(src, "js"))) if (!f.endsWith(".map")) fs.copyFileSync(path.join(src, "js", f), path.join(dest, "js", f));
fs.copyFileSync(path.join(src, "css", "main.css"), path.join(dest, "css", "main.css"));
console.log("Copied Label Studio editor to app/static/lsf");
