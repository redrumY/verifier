from __future__ import annotations

import json
from pathlib import Path

from harness.repo_scanner import RepoScanner


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_repo_scanner_detects_cra_frontend_and_express_backend(tmp_path: Path):
    write_json(
        tmp_path / "package.json",
        {
            "scripts": {"start": "node backend/server.js"},
            "dependencies": {"express": "^4.18.0"},
        },
    )
    write_json(
        tmp_path / "frontend/package.json",
        {
            "proxy": "http://localhost:5000",
            "scripts": {"start": "react-scripts start", "build": "react-scripts build"},
            "dependencies": {"react": "^18.0.0", "react-scripts": "5.0.1"},
        },
    )
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "frontend/src/App.js").write_text("export default function App() { return null }")

    facts = RepoScanner(tmp_path).scan()

    assert facts.frontend_stack == "create-react-app"
    assert facts.backend_stack == "express"
    assert facts.likely_frontend_dir == "frontend"
    assert facts.api_proxy == "http://localhost:5000"
    assert facts.build_commands == [["npm", "run", "build", "--prefix", "frontend"]]
    assert facts.dev_commands == [["npm", "run", "start", "--prefix", "frontend"]]
    assert "frontend/src/App.js" in facts.relevant_files


def test_repo_scanner_detects_vite_commands(tmp_path: Path):
    write_json(
        tmp_path / "package.json",
        {
            "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
            "dependencies": {"@vitejs/plugin-react": "^4.0.0", "react": "^18.0.0"},
            "devDependencies": {"vite": "^5.0.0", "vitest": "^1.0.0"},
        },
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("export default function App() { return null }")

    facts = RepoScanner(tmp_path).scan()

    assert facts.frontend_stack == "vite"
    assert facts.backend_stack == "unknown"
    assert facts.likely_frontend_dir == "."
    assert facts.build_commands == [["npm", "run", "build"]]
    assert facts.test_commands == [["npm", "run", "test"]]
    assert facts.dev_commands == [["npm", "run", "dev"]]
