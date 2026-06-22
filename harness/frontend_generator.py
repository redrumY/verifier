"""Generate complete Vite/React frontend projects from a project spec."""

from __future__ import annotations

import json
import re
from html import escape
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REQUIRED_FRONTEND_FILES = (
    "package.json",
    "index.html",
    "src/App.tsx",
    "src/main.tsx",
)


def _slugify(text: str, fallback: str = "generated-frontend") -> str:
    lowered = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:48] or fallback


@dataclass
class FrontendProjectSpec:
    name: str
    title: str
    description: str
    features: list[str] = field(default_factory=list)
    style_direction: str = "clean, responsive, product-quality interface"
    stack: dict[str, str] = field(default_factory=lambda: {
        "framework": "React",
        "language": "TypeScript",
        "bundler": "Vite",
    })
    acceptance_criteria: list[str] = field(default_factory=lambda: [
        "package.json exists",
        "src/App.tsx exists",
        "src/main.tsx exists",
        "index.html exists",
        "npm install succeeds",
        "npm run build succeeds",
        "page opens in a browser",
    ])
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GeneratedFrontendProject:
    project_dir: str
    spec_path: str
    files: list[str]
    install_command: str = "npm install"
    build_command: str = "npm run build"
    dev_command: str = "npm run dev -- --host 127.0.0.1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FrontendGenerator:
    """Deterministic scaffold generator for a complete frontend project."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def spec_from_natural_language(self, request: str, name: str | None = None) -> FrontendProjectSpec:
        title = self._title_from_request(request)
        features = self._features_from_request(request)
        return FrontendProjectSpec(
            name=_slugify(name or title),
            title=title,
            description=request.strip(),
            features=features,
            metadata={"source": "natural_language"},
        )

    def write_spec(self, spec: FrontendProjectSpec, project_dir: str | Path) -> Path:
        target_dir = self._resolve(project_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "spec.json"
        path.write_text(json.dumps(spec.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def generate_project(
        self,
        spec: FrontendProjectSpec,
        project_dir: str | Path,
        overwrite: bool = True,
    ) -> GeneratedFrontendProject:
        target_dir = self._resolve(project_dir)
        if target_dir.exists() and not overwrite:
            existing = [path for path in REQUIRED_FRONTEND_FILES if (target_dir / path).exists()]
            if existing:
                raise FileExistsError(f"project already contains generated files: {existing}")
        (target_dir / "src").mkdir(parents=True, exist_ok=True)

        files = {
            "spec.json": json.dumps(spec.to_dict(), indent=2, ensure_ascii=False) + "\n",
            "package.json": self._package_json(spec),
            "index.html": self._index_html(spec),
            "src/main.tsx": self._main_tsx(),
            "src/App.tsx": self._app_tsx(spec),
            "src/styles.css": self._styles_css(),
            "vite.config.ts": self._vite_config(),
            "tsconfig.json": self._tsconfig(),
        }
        for relative, content in files.items():
            path = target_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return GeneratedFrontendProject(
            project_dir=str(target_dir),
            spec_path=str(target_dir / "spec.json"),
            files=sorted(files.keys()),
        )

    def generate_from_natural_language(
        self,
        request: str,
        project_dir: str | Path,
        name: str | None = None,
    ) -> GeneratedFrontendProject:
        spec = self.spec_from_natural_language(request, name=name)
        return self.generate_project(spec, project_dir)

    def validate_minimum_delivery(self, project_dir: str | Path) -> dict[str, Any]:
        target_dir = self._resolve(project_dir)
        missing = [relative for relative in REQUIRED_FRONTEND_FILES if not (target_dir / relative).exists()]
        package_path = target_dir / "package.json"
        scripts: dict[str, Any] = {}
        dependencies: dict[str, Any] = {}
        dev_dependencies: dict[str, Any] = {}
        if package_path.exists():
            payload = json.loads(package_path.read_text(encoding="utf-8"))
            scripts = payload.get("scripts", {})
            dependencies = payload.get("dependencies", {})
            dev_dependencies = payload.get("devDependencies", {})
        return {
            "ok": not missing and "build" in scripts and "dev" in scripts,
            "missing_files": missing,
            "has_installable_package": package_path.exists() and bool(dependencies),
            "has_build_script": "build" in scripts,
            "has_dev_script": "dev" in scripts,
            "dependencies": sorted(dependencies.keys()),
            "dev_dependencies": sorted(dev_dependencies.keys()),
            "commands": {
                "install": "npm install",
                "build": "npm run build",
                "dev": "npm run dev -- --host 127.0.0.1",
            },
        }

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        root = self.root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"path escapes project root: {path}") from exc
        return resolved

    def _title_from_request(self, request: str) -> str:
        cleaned = re.sub(r"\s+", " ", request.strip())
        if not cleaned:
            return "Generated Frontend"
        sentence = re.split(r"[。.!?\n]", cleaned)[0].strip()
        return sentence[:80] or "Generated Frontend"

    def _features_from_request(self, request: str) -> list[str]:
        candidates = [
            "Responsive layout",
            "Clear primary action",
            "Project-specific content from the natural language request",
        ]
        lowered = request.lower()
        if any(word in lowered for word in ("dashboard", "数据", "看板")):
            candidates.append("Dashboard metrics section")
        if any(word in lowered for word in ("form", "表单", "login", "登录")):
            candidates.append("Input form section")
        if any(word in lowered for word in ("shop", "商城", "pricing", "价格")):
            candidates.append("Product or pricing cards")
        return candidates

    def _package_json(self, spec: FrontendProjectSpec) -> str:
        payload = {
            "name": spec.name,
            "version": "0.1.0",
            "private": True,
            "type": "module",
            "scripts": {
                "dev": "vite",
                "typecheck": "tsc --noEmit",
                "build": "vite build",
                "test": "npm run typecheck",
                "preview": "vite preview",
            },
            "dependencies": {
                "@vitejs/plugin-react": "^4.3.4",
                "vite": "^6.0.0",
                "typescript": "^5.7.2",
                "react": "^19.0.0",
                "react-dom": "^19.0.0",
            },
            "devDependencies": {
                "@types/react": "^19.0.0",
                "@types/react-dom": "^19.0.0",
            },
        }
        return json.dumps(payload, indent=2) + "\n"

    def _index_html(self, spec: FrontendProjectSpec) -> str:
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{self._jsx_text(spec.title)}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

    def _main_tsx(self) -> str:
        return """import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
"""

    def _app_tsx(self, spec: FrontendProjectSpec) -> str:
        features = "\n".join(
            f"    {{ title: {json.dumps(feature)}, detail: 'Generated from the project specification.' }},"
            for feature in spec.features
        )
        return f"""const features = [
{features}
];

export default function App() {{
  return (
    <main className="app-shell">
      <section className="hero">
        <p className="eyebrow">Generated frontend project</p>
        <h1>{self._jsx_text(spec.title)}</h1>
        <p className="lede">{self._jsx_text(spec.description)}</p>
        <div className="actions">
          <a href="#features" className="primary-action">View features</a>
          <a href="#status" className="secondary-action">Check build target</a>
        </div>
      </section>

      <section id="features" className="feature-grid" aria-label="Generated features">
        {{features.map((feature) => (
          <article className="feature-card" key={{feature.title}}>
            <h2>{{feature.title}}</h2>
            <p>{{feature.detail}}</p>
          </article>
        ))}}
      </section>

      <section id="status" className="status-panel">
        <h2>Delivery target</h2>
        <ul>
          <li>Vite + React + TypeScript project</li>
          <li>Install with <code>npm install</code></li>
          <li>Build with <code>npm run build</code></li>
          <li>Open locally with <code>npm run dev</code></li>
        </ul>
      </section>
    </main>
  );
}}
"""

    def _styles_css(self) -> str:
        return """:root {
  color: #17202a;
  background: #f7f9fb;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
}

a {
  color: inherit;
}

.app-shell {
  min-height: 100vh;
  padding: 48px clamp(20px, 5vw, 72px);
}

.hero {
  max-width: 920px;
  padding: 56px 0 40px;
}

.eyebrow {
  margin: 0 0 12px;
  color: #47616f;
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  max-width: 860px;
  font-size: clamp(2.4rem, 7vw, 5.4rem);
  line-height: 0.98;
}

.lede {
  max-width: 760px;
  margin: 24px 0 0;
  color: #40515c;
  font-size: 1.14rem;
  line-height: 1.7;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 32px;
}

.primary-action,
.secondary-action {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  padding: 0 18px;
  font-weight: 700;
  text-decoration: none;
}

.primary-action {
  background: #126b5d;
  color: white;
}

.secondary-action {
  border: 1px solid #c8d3d8;
  background: white;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-top: 24px;
}

.feature-card,
.status-panel {
  border: 1px solid #d9e2e7;
  border-radius: 8px;
  background: white;
  padding: 20px;
}

.feature-card h2,
.status-panel h2 {
  margin: 0 0 10px;
  font-size: 1rem;
}

.feature-card p,
.status-panel li {
  color: #53656f;
  line-height: 1.6;
}

.status-panel {
  margin-top: 16px;
}

code {
  border-radius: 6px;
  background: #edf2f4;
  padding: 2px 5px;
}
"""

    def _vite_config(self) -> str:
        return """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
});
"""

    def _tsconfig(self) -> str:
        payload = {
            "compilerOptions": {
                "target": "ES2020",
                "useDefineForClassFields": True,
                "lib": ["DOM", "DOM.Iterable", "ES2020"],
                "allowJs": False,
                "skipLibCheck": True,
                "esModuleInterop": True,
                "allowSyntheticDefaultImports": True,
                "strict": True,
                "forceConsistentCasingInFileNames": True,
                "module": "ESNext",
                "moduleResolution": "Node",
                "resolveJsonModule": True,
                "isolatedModules": True,
                "noEmit": True,
                "jsx": "react-jsx",
            },
            "include": ["src"],
            "references": [],
        }
        return json.dumps(payload, indent=2) + "\n"

    def _jsx_text(self, text: str) -> str:
        return escape(text, quote=False)
