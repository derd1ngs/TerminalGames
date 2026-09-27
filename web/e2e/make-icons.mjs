// Regenerate the PNG favicons from web/favicon.svg (the source of truth):
//   cd web/e2e && npm ci && npx playwright install firefox
//   node make-icons.mjs ../favicon.svg ../favicon-32.png ../apple-touch-icon.png
import { firefox } from "playwright";
import { readFileSync, writeFileSync } from "node:fs";
const [svgPath, out32, out180] = process.argv.slice(2);
const svg = readFileSync(svgPath, "utf8");
// iOS masks home-screen icons itself: full-bleed square, no rounding or border.
const apple = svg
  .replace('<rect width="64" height="64" rx="13"', '<rect width="64" height="64"')
  .replace(/\n\s*<rect x="1.5"[^\n]*/, "");
const browser = await firefox.launch();
const page = await browser.newPage();
async function rasterize(markup, size, path) {
  const dataUrl = await page.evaluate(
    async ([markup, size]) => {
      const img = new Image();
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
      await img.decode();
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      canvas.getContext("2d").drawImage(img, 0, 0, size, size);
      return canvas.toDataURL("image/png");
    },
    [markup, size],
  );
  writeFileSync(path, Buffer.from(dataUrl.split(",")[1], "base64"));
}
await rasterize(svg, 32, out32);
await rasterize(apple, 180, out180);
await browser.close();
