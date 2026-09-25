import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: { root: __dirname },
  outputFileTracingRoot: __dirname,
  experimental: {
    cpus: 2,
    proxyClientMaxBodySize: "2gb",
    serverActions: {
      bodySizeLimit: "2gb",
    },
  },
  // Логотипы платформы (`api/settings/assets` разрешает загрузку `.svg`) лежат в
  // `public/branding` и раздаются статикой в обход роутов — CSP из
  // `uploads/[...path]/route.ts` сюда не достаёт. При прямом открытии
  // `/branding/logo.svg` скрипт внутри SVG исполнился бы в origin приложения.
  // `headers()` в Next применяется и к статике из `public/`
  // ("checked before the filesystem"), поэтому изоляция навешивается здесь.
  //
  // Логотип отображается через `<img src>`, а для под-ресурса `<img>` браузер
  // и так не исполняет скрипты SVG, и заголовок sandbox рендерингу не мешает —
  // ломается только исполнение при переходе на файл как на документ.
  async headers() {
    return [
      {
        source: "/branding/:path*",
        headers: [
          // sandbox без allow-scripts: логотипу скрипты не нужны, в отличие от
          // HTML5-плеера презентаций в /uploads, которому allow-scripts оставлен.
          { key: "Content-Security-Policy", value: "sandbox" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
