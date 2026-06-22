from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.frontend_generator import FrontendGenerator, REQUIRED_FRONTEND_FILES


def test_generator_creates_complete_vite_project(tmp_path: Path):
    generator = FrontendGenerator(tmp_path)

    generated = generator.generate_from_natural_language(
        "生成一个有数据看板和登录表单的 React 页面",
        "generated/frontend-app",
    )

    project_dir = Path(generated.project_dir)
    assert project_dir.exists()
    for relative in REQUIRED_FRONTEND_FILES:
        assert (project_dir / relative).exists(), relative
    assert (project_dir / "spec.json").exists()
    assert (project_dir / "src/styles.css").exists()
    assert (project_dir / "vite.config.ts").exists()
    assert "src/App.tsx" in generated.files


def test_generated_package_has_install_build_and_dev_scripts(tmp_path: Path):
    generator = FrontendGenerator(tmp_path)
    generator.generate_from_natural_language("生成一个 React 页面", "app")

    package_json = json.loads((tmp_path / "app" / "package.json").read_text())

    assert package_json["scripts"]["dev"] == "vite"
    assert package_json["scripts"]["typecheck"] == "tsc --noEmit"
    assert package_json["scripts"]["build"] == "vite build"
    assert package_json["scripts"]["test"] == "npm run typecheck"
    assert "react" in package_json["dependencies"]
    assert "vite" in package_json["dependencies"]
    assert "@types/react" in package_json["devDependencies"]


def test_validate_minimum_delivery_reports_commands(tmp_path: Path):
    generator = FrontendGenerator(tmp_path)
    generator.generate_from_natural_language("生成一个 React 页面", "app")

    result = generator.validate_minimum_delivery("app")

    assert result["ok"] is True
    assert result["missing_files"] == []
    assert result["has_installable_package"] is True
    assert result["has_build_script"] is True
    assert result["has_dev_script"] is True
    assert result["commands"] == {
        "install": "npm install",
        "build": "npm run build",
        "dev": "npm run dev -- --host 127.0.0.1",
    }


def test_spec_captures_natural_language_features(tmp_path: Path):
    generator = FrontendGenerator(tmp_path)

    spec = generator.spec_from_natural_language("做一个 dashboard 登录表单页面")

    assert spec.stack == {
        "framework": "React",
        "language": "TypeScript",
        "bundler": "Vite",
    }
    assert "Dashboard metrics section" in spec.features
    assert "Input form section" in spec.features


def test_frontend_generator_rejects_paths_outside_root(tmp_path: Path):
    generator = FrontendGenerator(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        generator.generate_from_natural_language("生成一个 React 页面", tmp_path.parent / "app")


def test_generator_escapes_user_text_in_tsx(tmp_path: Path):
    generator = FrontendGenerator(tmp_path)

    generator.generate_from_natural_language("生成 <Admin> React 页面", "app")

    app = (tmp_path / "app" / "src/App.tsx").read_text()
    assert "生成 &lt;Admin&gt; React 页面" in app
    assert "<h1>生成 <Admin> React 页面</h1>" not in app
