"""Docker sandbox runner for isolated project verification."""

from __future__ import annotations

import fnmatch
import json
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .verifier import CommandResult


SANDBOX_STATUSES = {"prepared", "passed", "failed", "skipped"}


@dataclass
class BrowserViewport:
    name: str
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserProfile:
    enabled: bool = True
    route: str = "/"
    port: int = 4173
    check_console_errors: bool = True
    screenshots: bool = True
    viewports: list[BrowserViewport] = field(default_factory=lambda: [
        BrowserViewport("desktop", 1440, 900),
        BrowserViewport("mobile", 390, 844),
    ])

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["viewports"] = [viewport.to_dict() for viewport in self.viewports]
        return payload


@dataclass
class TestCommand:
    name: str
    command: list[str]
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestProfile:
    name: str
    project_type: str
    docker_image: str
    commands: list[TestCommand]
    browser: BrowserProfile = field(default_factory=BrowserProfile)
    timeout_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["commands"] = [command.to_dict() for command in self.commands]
        payload["browser"] = self.browser.to_dict()
        return payload


@dataclass
class SandboxRun:
    run_id: str
    source_project_dir: str
    run_dir: str
    project_dir: str
    sandbox_dir: str
    outputs_dir: str
    profile_path: str
    dockerfile_path: str
    compose_path: str
    run_script_path: str
    patch_path: str
    profile: TestProfile
    status: str = "prepared"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["profile"] = self.profile.to_dict()
        return payload


@dataclass
class SandboxExecutionResult:
    run_id: str
    status: str
    command: list[str]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    run_dir: str = ""
    report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DockerRunner = Callable[[list[str], Path, int], CommandResult]


def frontend_ui_component_profile() -> TestProfile:
    return TestProfile(
        name="frontend-ui-component",
        project_type="frontend",
        docker_image="mcr.microsoft.com/playwright:v1.49.1-noble",
        commands=[
            TestCommand("install", ["npm", "install"]),
            TestCommand("typecheck", ["npm", "run", "typecheck"], required=False),
            TestCommand("build", ["npm", "run", "build"]),
            TestCommand("test", ["npm", "run", "test"], required=False),
        ],
        browser=BrowserProfile(),
        metadata={
            "purpose": "isolate UI component changes before touching the original project",
        },
    )


class DockerSandboxRunner:
    """Create and run Docker-based local-CI sandboxes."""

    DEFAULT_IGNORE = {
        ".git",
        ".sandbox",
        ".context",
        ".logs",
        "node_modules",
        "dist",
        "build",
        "coverage",
        ".next",
        ".vite",
        "outputs",
    }

    def __init__(
        self,
        root: str | Path,
        sandbox_root: str | Path = ".sandbox/runs",
        runner: DockerRunner | None = None,
        command_timeout: int = 900,
    ):
        self.root = Path(root)
        self.sandbox_root = self._resolve(sandbox_root)
        self.runner = runner or self._run_docker_command
        self.command_timeout = command_timeout
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def prepare_run(
        self,
        source_project_dir: str | Path,
        profile: TestProfile | None = None,
        patch_text: str | None = None,
        run_id: str | None = None,
    ) -> SandboxRun:
        source = self._resolve(source_project_dir)
        if not source.is_dir():
            raise FileNotFoundError(f"source project not found: {source_project_dir}")

        profile = profile or frontend_ui_component_profile()
        run_id = run_id or f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        run_dir = self.sandbox_root / run_id
        project_dir = run_dir / "project"
        sandbox_dir = run_dir / "sandbox"
        outputs_dir = run_dir / "outputs"
        for directory in (sandbox_dir, outputs_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self._copy_project(source, project_dir)
        patch_path = run_dir / "patch.diff"
        patch_path.write_text(patch_text or "", encoding="utf-8")
        profile_path = run_dir / "test-profile.json"
        profile_path.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        dockerfile_path = sandbox_dir / "Dockerfile"
        compose_path = sandbox_dir / "docker-compose.yml"
        run_script_path = sandbox_dir / "run_tests.sh"
        browser_check_path = sandbox_dir / "browser_check.mjs"
        write_report_path = sandbox_dir / "write_report.mjs"

        dockerfile_path.write_text(self._dockerfile(profile), encoding="utf-8")
        compose_path.write_text(self._compose_file(), encoding="utf-8")
        run_script_path.write_text(self._run_script(profile), encoding="utf-8")
        run_script_path.chmod(0o755)
        browser_check_path.write_text(self._browser_check_script(profile), encoding="utf-8")
        write_report_path.write_text(self._write_report_script(), encoding="utf-8")

        sandbox_run = SandboxRun(
            run_id=run_id,
            source_project_dir=str(source),
            run_dir=str(run_dir),
            project_dir=str(project_dir),
            sandbox_dir=str(sandbox_dir),
            outputs_dir=str(outputs_dir),
            profile_path=str(profile_path),
            dockerfile_path=str(dockerfile_path),
            compose_path=str(compose_path),
            run_script_path=str(run_script_path),
            patch_path=str(patch_path),
            profile=profile,
        )
        self._write_manifest(sandbox_run)
        return sandbox_run

    def load_run(self, run_dir: str | Path) -> SandboxRun:
        target = self._resolve(run_dir)
        manifest = json.loads((target / "sandbox-run.json").read_text(encoding="utf-8"))
        profile_payload = manifest["profile"]
        browser_payload = profile_payload["browser"]
        profile = TestProfile(
            name=profile_payload["name"],
            project_type=profile_payload["project_type"],
            docker_image=profile_payload["docker_image"],
            commands=[TestCommand(**command) for command in profile_payload["commands"]],
            browser=BrowserProfile(
                enabled=browser_payload.get("enabled", True),
                route=browser_payload.get("route", "/"),
                port=browser_payload.get("port", 4173),
                check_console_errors=browser_payload.get("check_console_errors", True),
                screenshots=browser_payload.get("screenshots", True),
                viewports=[BrowserViewport(**viewport) for viewport in browser_payload.get("viewports", [])],
            ),
            timeout_seconds=profile_payload.get("timeout_seconds", 300),
            metadata=profile_payload.get("metadata", {}),
        )
        manifest["profile"] = profile
        return SandboxRun(**manifest)

    def run(self, sandbox_run: SandboxRun | str | Path) -> SandboxExecutionResult:
        if not isinstance(sandbox_run, SandboxRun):
            sandbox_run = self.load_run(sandbox_run)
        command = [
            "docker",
            "compose",
            "-f",
            str(Path(sandbox_run.compose_path)),
            "run",
            "--rm",
            "verifier",
        ]
        result = self.runner(command, Path(sandbox_run.run_dir), sandbox_run.profile.timeout_seconds)
        status = "passed" if result.status == "passed" else "failed"
        report_path = Path(sandbox_run.outputs_dir) / "verification-report.json"
        execution = SandboxExecutionResult(
            run_id=sandbox_run.run_id,
            status=status,
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            run_dir=sandbox_run.run_dir,
            report_path=str(report_path) if report_path.exists() else None,
        )
        (Path(sandbox_run.outputs_dir) / "sandbox-result.json").write_text(
            json.dumps(execution.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return execution

    def _copy_project(self, source: Path, project_dir: Path):
        if project_dir.exists():
            shutil.rmtree(project_dir)
        shutil.copytree(source, project_dir, ignore=self._ignore)

    def _ignore(self, directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in self.DEFAULT_IGNORE:
                ignored.add(name)
                continue
            if fnmatch.fnmatch(name, "*.log"):
                ignored.add(name)
        return ignored

    def _dockerfile(self, profile: TestProfile) -> str:
        return f"""FROM {profile.docker_image}

WORKDIR /workspace/project
ENV CI=1
"""

    def _compose_file(self) -> str:
        return """services:
  verifier:
    build:
      context: ..
      dockerfile: sandbox/Dockerfile
    working_dir: /workspace/project
    environment:
      - CI=1
    volumes:
      - ../project:/workspace/project
      - ../outputs:/workspace/outputs
      - ../test-profile.json:/workspace/test-profile.json:ro
      - ../patch.diff:/workspace/patch.diff:ro
      - ../sandbox:/workspace/sandbox:ro
    command: ["bash", "/workspace/sandbox/run_tests.sh"]
"""

    def _run_script(self, profile: TestProfile) -> str:
        command_blocks = "\n".join(
            self._shell_command_block(command) for command in profile.commands
        )
        browser_block = ""
        if profile.browser.enabled:
            browser_block = f"""
if [ "$BUILD_STATUS" = "passed" ]; then
  echo "[sandbox] starting browser verification"
  (npm run dev -- --host 0.0.0.0 --port {profile.browser.port} > "$OUTPUTS_DIR/dev-server.log" 2>&1) &
  SERVER_PID=$!
  trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
  node -e "import('playwright')" >/dev/null 2>&1 || npm install --no-save playwright
  if node /workspace/sandbox/browser_check.mjs "http://127.0.0.1:{profile.browser.port}{profile.browser.route}" "$OUTPUTS_DIR"; then
    true
  else
    OVERALL_STATUS=failed
  fi
else
  echo '{{"status":"skipped","skipped_reason":"build did not pass","console_errors":[],"screenshot":null}}' > "$OUTPUTS_DIR/browser-report.json"
fi
"""
        return f"""#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR=/workspace/project
OUTPUTS_DIR=/workspace/outputs
STEPS_PATH="$OUTPUTS_DIR/steps.jsonl"
mkdir -p "$OUTPUTS_DIR"
rm -f "$STEPS_PATH"
cd "$PROJECT_DIR"

if [ -s /workspace/patch.diff ]; then
  echo "[sandbox] applying patch.diff"
  git apply /workspace/patch.diff
fi

record_step() {{
  local name="$1"
  local status="$2"
  local exit_code="$3"
  local stdout_path="$4"
  local stderr_path="$5"
  printf '{{"name":"%s","status":"%s","exit_code":%s,"stdout_path":"%s","stderr_path":"%s"}}\\n' "$name" "$status" "$exit_code" "$stdout_path" "$stderr_path" >> "$STEPS_PATH"
}}

run_step() {{
  local name="$1"
  shift
  local stdout_path="$OUTPUTS_DIR/$name.stdout.log"
  local stderr_path="$OUTPUTS_DIR/$name.stderr.log"
  echo "[sandbox] running $name: $*"
  if "$@" > "$stdout_path" 2> "$stderr_path"; then
    record_step "$name" "passed" 0 "$stdout_path" "$stderr_path"
    return 0
  else
    local exit_code=$?
    record_step "$name" "failed" "$exit_code" "$stdout_path" "$stderr_path"
    return "$exit_code"
  fi
}}

BUILD_STATUS=skipped
OVERALL_STATUS=passed

{command_blocks}
{browser_block}

node /workspace/sandbox/write_report.mjs "$OUTPUTS_DIR"
if [ "$OVERALL_STATUS" != "passed" ]; then
  exit 1
fi
"""

    def _shell_command_block(self, command: TestCommand) -> str:
        parts = " ".join(shlex.quote(part) for part in command.command)
        if command.required:
            return f"""if run_step {shlex.quote(command.name)} {parts}; then
  if [ {shlex.quote(command.name)} = "build" ]; then BUILD_STATUS=passed; fi
else
  OVERALL_STATUS=failed
  if [ {shlex.quote(command.name)} = "build" ]; then BUILD_STATUS=failed; fi
fi
"""
        return f"""if npm run | grep -q " {command.name}"; then
  if run_step {shlex.quote(command.name)} {parts}; then
    if [ {shlex.quote(command.name)} = "build" ]; then BUILD_STATUS=passed; fi
  else
    OVERALL_STATUS=failed
    if [ {shlex.quote(command.name)} = "build" ]; then BUILD_STATUS=failed; fi
  fi
else
  record_step {shlex.quote(command.name)} "skipped" 0 "$OUTPUTS_DIR/{command.name}.stdout.log" "$OUTPUTS_DIR/{command.name}.stderr.log"
fi
"""

    def _browser_check_script(self, profile: TestProfile) -> str:
        viewports = json.dumps([viewport.to_dict() for viewport in profile.browser.viewports], ensure_ascii=False)
        check_console_errors = "true" if profile.browser.check_console_errors else "false"
        screenshots = "true" if profile.browser.screenshots else "false"
        return f"""import fs from 'node:fs';
import path from 'node:path';
import {{ chromium }} from 'playwright';

const url = process.argv[2];
const outputsDir = process.argv[3];
const viewports = {viewports};
const checkConsoleErrors = {check_console_errors};
const screenshots = {screenshots};
const consoleErrors = [];
const screenshotPaths = [];

const browser = await chromium.launch({{ headless: true }});
try {{
  for (const viewport of viewports) {{
    const page = await browser.newPage({{ viewport: {{ width: viewport.width, height: viewport.height }} }});
    if (checkConsoleErrors) {{
      page.on('console', (msg) => {{
        if (msg.type() === 'error') consoleErrors.push(msg.text());
      }});
      page.on('pageerror', (error) => consoleErrors.push(error.message));
    }}
    await page.goto(url, {{ waitUntil: 'networkidle', timeout: 30000 }});
    if (screenshots) {{
      const file = path.join(outputsDir, `screenshot-${{viewport.name}}.png`);
      await page.screenshot({{ path: file, fullPage: true }});
      screenshotPaths.push(file);
    }}
    await page.close();
  }}
}} finally {{
  await browser.close();
}}

const status = consoleErrors.length ? 'failed' : 'passed';
const report = {{
  status,
  url,
  console_errors: consoleErrors,
  screenshot: screenshotPaths[0] ?? null,
  screenshots: screenshotPaths,
}};
fs.writeFileSync(path.join(outputsDir, 'browser-report.json'), JSON.stringify(report, null, 2));
if (status !== 'passed') process.exit(1);
"""

    def _write_report_script(self) -> str:
        return """import fs from 'node:fs';
import path from 'node:path';

const outputsDir = process.argv[2];
const stepsPath = path.join(outputsDir, 'steps.jsonl');
const steps = fs.existsSync(stepsPath)
  ? fs.readFileSync(stepsPath, 'utf8').trim().split('\\n').filter(Boolean).map((line) => JSON.parse(line))
  : [];
const byName = Object.fromEntries(steps.map((step) => [step.name, step]));
const browserPath = path.join(outputsDir, 'browser-report.json');
const browser = fs.existsSync(browserPath)
  ? JSON.parse(fs.readFileSync(browserPath, 'utf8'))
  : { status: 'skipped', console_errors: [], screenshot: null };
const report = {
  install: byName.install?.status ?? 'skipped',
  typecheck: byName.typecheck?.status ?? 'skipped',
  build: byName.build?.status ?? 'skipped',
  tests: byName.test?.status ?? 'skipped',
  browser: browser.status ?? 'skipped',
  console_errors: browser.console_errors ?? [],
  screenshot: browser.screenshot ?? null,
  screenshots: browser.screenshots ?? [],
  steps,
};
fs.writeFileSync(path.join(outputsDir, 'verification-report.json'), JSON.stringify(report, null, 2));
if (['install', 'typecheck', 'build', 'test'].some((name) => byName[name]?.status === 'failed') || report.browser === 'failed') {
  process.exit(1);
}
"""

    def _write_manifest(self, sandbox_run: SandboxRun):
        (Path(sandbox_run.run_dir) / "sandbox-run.json").write_text(
            json.dumps(sandbox_run.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _run_docker_command(self, command: list[str], cwd: Path, timeout: int) -> CommandResult:
        start = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            status = "passed" if completed.returncode == 0 else "failed"
            return CommandResult(
                name="docker_sandbox",
                command=command,
                status=status,
                returncode=completed.returncode,
                stdout=completed.stdout[-20000:],
                stderr=completed.stderr[-20000:],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                name="docker_sandbox",
                command=command,
                status="failed",
                stdout=(exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
                stderr=f"timeout after {timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

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
