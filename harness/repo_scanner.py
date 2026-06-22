"""Repository fact scanner for dynamic coding-agent planning."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


IGNORED_DIRS = {
    ".git",
    ".agent-probe",
    ".context",
    ".next",
    ".sandbox",
    ".tasks",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "outputs",
}


@dataclass
class PackageFacts:
    path: str
    name: str = ""
    scripts: dict[str, str] = field(default_factory=dict)
    dependencies: dict[str, str] = field(default_factory=dict)
    dev_dependencies: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepoFacts:
    root: str
    packages: list[PackageFacts] = field(default_factory=list)
    frontend_stack: str = "unknown"
    backend_stack: str = "unknown"
    package_manager: str = "unknown"
    source_dirs: list[str] = field(default_factory=list)
    likely_frontend_dir: str | None = None
    likely_backend_dir: str | None = None
    api_proxy: str | None = None
    build_commands: list[list[str]] = field(default_factory=list)
    test_commands: list[list[str]] = field(default_factory=list)
    dev_commands: list[list[str]] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["packages"] = [package.to_dict() for package in self.packages]
        return payload


class RepoScanner:
    """Extract coarse, deterministic repo facts before planning."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def scan(self, max_depth: int = 4) -> RepoFacts:
        packages = self._find_packages(max_depth=max_depth)
        frontend_package = self._select_frontend_package(packages)
        backend_package = self._select_backend_package(packages)
        facts = RepoFacts(
            root=str(self.root),
            packages=packages,
            frontend_stack=self._frontend_stack(frontend_package),
            backend_stack=self._backend_stack(packages),
            package_manager=self._package_manager(),
            source_dirs=self._source_dirs(),
            likely_frontend_dir=self._relative(frontend_package.path).parent.as_posix()
            if frontend_package else None,
            likely_backend_dir=self._backend_dir(backend_package),
            api_proxy=frontend_package.proxy if frontend_package else None,
        )
        facts.build_commands = self._commands_for(frontend_package, "build")
        facts.test_commands = self._commands_for(frontend_package, "test")
        facts.dev_commands = self._dev_commands(frontend_package)
        facts.relevant_files = self._relevant_files(facts)
        facts.warnings = self._warnings(facts)
        return facts

    def _find_packages(self, max_depth: int) -> list[PackageFacts]:
        packages: list[PackageFacts] = []
        for path in sorted(self.root.rglob("package.json")):
            if self._ignored(path):
                continue
            try:
                depth = len(path.relative_to(self.root).parents) - 1
            except ValueError:
                continue
            if depth > max_depth:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            packages.append(PackageFacts(
                path=str(path),
                name=payload.get("name", ""),
                scripts=payload.get("scripts", {}),
                dependencies=payload.get("dependencies", {}),
                dev_dependencies=payload.get("devDependencies", {}),
                proxy=payload.get("proxy"),
            ))
        return packages

    def _select_frontend_package(self, packages: list[PackageFacts]) -> PackageFacts | None:
        scored = []
        for package in packages:
            deps = self._deps(package)
            score = 0
            if "react" in deps or "vue" in deps or "svelte" in deps:
                score += 3
            if "vite" in deps or "react-scripts" in deps or "next" in deps:
                score += 3
            if "build" in package.scripts:
                score += 1
            if "frontend" in Path(package.path).parts or "client" in Path(package.path).parts:
                score += 2
            if score:
                scored.append((score, package))
        return max(scored, key=lambda item: item[0])[1] if scored else None

    def _select_backend_package(self, packages: list[PackageFacts]) -> PackageFacts | None:
        for package in packages:
            deps = self._deps(package)
            if any(dep in deps for dep in ("express", "fastify", "koa", "mongoose")):
                return package
        return packages[0] if packages else None

    def _frontend_stack(self, package: PackageFacts | None) -> str:
        if package is None:
            return "unknown"
        deps = self._deps(package)
        if "vite" in deps:
            return "vite"
        if "react-scripts" in deps:
            return "create-react-app"
        if "next" in deps:
            return "next"
        if "react" in deps:
            return "react"
        return "unknown"

    def _backend_stack(self, packages: list[PackageFacts]) -> str:
        deps = {}
        for package in packages:
            deps.update(self._deps(package))
        if "express" in deps:
            return "express"
        if "fastify" in deps:
            return "fastify"
        if (self.root / "pyproject.toml").exists() or (self.root / "requirements.txt").exists():
            return "python"
        return "unknown"

    def _package_manager(self) -> str:
        if (self.root / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (self.root / "yarn.lock").exists():
            return "yarn"
        if (self.root / "package-lock.json").exists():
            return "npm"
        return "unknown"

    def _source_dirs(self) -> list[str]:
        candidates = []
        for name in ("src", "frontend/src", "client/src", "app", "backend", "server"):
            path = self.root / name
            if path.exists():
                candidates.append(name)
        return candidates

    def _backend_dir(self, package: PackageFacts | None) -> str | None:
        if package is None:
            return None
        path = self._relative(package.path).parent
        if path.as_posix() == ".":
            for name in ("backend", "server", "api"):
                if (self.root / name).exists():
                    return name
        return path.as_posix()

    def _commands_for(self, package: PackageFacts | None, script: str) -> list[list[str]]:
        if package is None or script not in package.scripts:
            return []
        cwd = self._relative(package.path).parent.as_posix()
        command = ["npm", "run", script]
        if cwd not in ("", "."):
            command.extend(["--prefix", cwd])
        return [command]

    def _dev_commands(self, package: PackageFacts | None) -> list[list[str]]:
        if package is None:
            return []
        if "dev" in package.scripts:
            script = "dev"
        elif "start" in package.scripts:
            script = "start"
        else:
            return []
        cwd = self._relative(package.path).parent.as_posix()
        command = ["npm", "run", script]
        if cwd not in ("", "."):
            command.extend(["--prefix", cwd])
        return [command]

    def _relevant_files(self, facts: RepoFacts) -> list[str]:
        candidates = [
            "package.json",
            "frontend/package.json",
            "client/package.json",
            "src/App.tsx",
            "src/App.jsx",
            "src/App.js",
            "frontend/src/App.tsx",
            "frontend/src/App.jsx",
            "frontend/src/App.js",
            "frontend/src/pages/Dashboard.jsx",
            "frontend/src/pages/Dashboard.tsx",
            "frontend/src/features/goals/goalSlice.js",
            "frontend/src/features/goals/goalService.js",
            "backend/routes/goalRoutes.js",
            "backend/controllers/goalController.js",
        ]
        return [item for item in candidates if (self.root / item).exists()]

    def _warnings(self, facts: RepoFacts) -> list[str]:
        warnings = []
        if facts.frontend_stack == "unknown":
            warnings.append("frontend stack not detected")
        if not facts.build_commands:
            warnings.append("no frontend build command detected")
        if facts.backend_stack != "unknown" and not facts.api_proxy:
            warnings.append("backend detected but frontend proxy/api base URL not detected")
        return warnings

    def _deps(self, package: PackageFacts) -> dict[str, str]:
        return {**package.dependencies, **package.dev_dependencies}

    def _relative(self, path: str | Path) -> Path:
        return Path(path).resolve().relative_to(self.root)

    def _ignored(self, path: Path) -> bool:
        return any(part in IGNORED_DIRS for part in path.relative_to(self.root).parts)
