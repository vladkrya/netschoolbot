// Сборка ретро-сайта: исходники netschool-retro (src/, без изменений) →
// app/web/static/retro/app.js и app.css. Запуск из корня репозитория:
//   npm i --prefix /tmp/esb esbuild@0.24.0
//   ESBUILD=/tmp/esb/node_modules/esbuild/lib/main.js node retro/build.mjs
// Отличия от их Vite-сборки — две подмены при сборке, а не в исходниках:
// - окно входа (src/login.ts) заменено на bridge/login.ts: вход — в приложении;
// - картинки /icons/… берутся из нашей статики (адрес даёт retro.html).
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const esbuild = await import(process.env.ESBUILD || "esbuild");
const here = path.dirname(fileURLToPath(import.meta.url));

const bridge = {
  name: "netschoolbot-bridge",
  setup(build) {
    build.onResolve({ filter: /^\.\/login$/ }, () => ({ path: path.join(here, "bridge", "login.ts") }));
    build.onLoad({ filter: /src[\\/]util\.ts$/ }, async (args) => ({
      contents: (await readFile(args.path, "utf8")).replace('src="/icons/', 'src="${globalThis.RETRO_ICONS}'),
      loader: "ts",
    }));
  },
};

await esbuild.build({
  entryPoints: { app: path.join(here, "src", "main.ts") },
  outdir: path.join(here, "..", "app", "web", "static", "retro"),
  bundle: true,
  format: "esm",
  target: "es2022",
  minify: true,
  legalComments: "none",
  plugins: [bridge],
  logLevel: "info",
});
