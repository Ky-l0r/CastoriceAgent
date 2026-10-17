// tsc 的 rootDir=electron 会把 electron/main/index.ts 编到 dist/main/main/，
// 而 package.json 的入口声明是 dist/main/index.js。此脚本在 build 末尾
// 把 dist/main/main/* 上提一层，保持磁盘结构与入口声明一致。
const fs = require("node:fs");
const path = require("node:path");

const nested = path.join(__dirname, "..", "dist", "main", "main");
if (!fs.existsSync(nested)) {
  console.log("[flatten-main-dist] dist/main/main 不存在，跳过（无需处理）");
  process.exit(0);
}
for (const entry of fs.readdirSync(nested, { withFileTypes: true })) {
  const from = path.join(nested, entry.name);
  const to = path.join(nested, "..", entry.name);
  fs.cpSync(from, to, { recursive: true });
}
fs.rmSync(nested, { recursive: true, force: true });
console.log("[flatten-main-dist] 已将 dist/main/main/* 上提到 dist/main/");
