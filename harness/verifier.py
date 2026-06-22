"""Project verification runtime for generated coding-agent outputs."""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


VERIFICATION_STATUSES = {"passed", "failed", "skipped"}


@dataclass
class CommandResult:
    name: str
    command: list[str]
    status: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserResult:
    status: str
    url: str | None = None
    console_errors: list[str] = field(default_factory=list)
    screenshot: str | None = None
    skipped_reason: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationReport:
    project_type: str
    project_dir: str
    install: str = "skipped"
    typecheck: str = "skipped"
    build: str = "skipped"
    tests: str = "skipped"
    browser: str = "skipped"
    console_errors: list[str] = field(default_factory=list)
    screenshot: str | None = None
    commands: list[CommandResult] = field(default_factory=list)
    browser_result: BrowserResult | None = None
    report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["commands"] = [command.to_dict() for command in self.commands]
        payload["browser_result"] = self.browser_result.to_dict() if self.browser_result else None
        return payload

    def summary(self) -> dict[str, Any]:
        return {
            "build": self.build,
            "tests": self.tests,
            "browser": self.browser,
            "console_errors": self.console_errors,
            "screenshot": self.screenshot,
        }


Runner = Callable[[list[str], Path, int], CommandResult]
BrowserRunner = Callable[[Path, Path], BrowserResult]


class Verifier:
    """Detect project type and run the matching verification workflow."""

    def __init__(
        self,
        root: str | Path,
        outputs_dir: str | Path = "outputs",
        command_timeout: int = 120,
        runner: Runner | None = None,
        browser_runner: BrowserRunner | None = None,
    ):
        self.root = Path(root)
        self.outputs_dir = self._resolve(outputs_dir)
        self.command_timeout = command_timeout
        self.runner = runner or self._run_command
        self.browser_runner = browser_runner
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def detect_project_type(self, project_dir: str | Path) -> str:
        target_dir = self._resolve(project_dir)
        package_json = target_dir / "package.json"
        if package_json.exists():
            payload = json.loads(package_json.read_text(encoding="utf-8"))
            dependencies = {
                **payload.get("dependencies", {}),
                **payload.get("devDependencies", {}),
            }
            scripts = payload.get("scripts", {})
            if "vite" in dependencies or "build" in scripts or (target_dir / "src/App.tsx").exists():
                return "frontend"
            return "node"
        if (target_dir / "pyproject.toml").exists() or (target_dir / "requirements.txt").exists():
            return "python"
        return "unknown"

    def verify(self, project_dir: str | Path, project_type: str | None = None) -> VerificationReport:
        detected = project_type or self.detect_project_type(project_dir)
        if detected == "frontend":
            return self.verify_frontend(project_dir)
        report = VerificationReport(
            project_type=detected,
            project_dir=str(self._resolve(project_dir)),
        )
        report.browser_result = BrowserResult(
            status="skipped",
            skipped_reason=f"unsupported project type: {detected}",
        )
        return self._write_report(report)

    def verify_frontend(self, project_dir: str | Path) -> VerificationReport:
        target_dir = self._resolve(project_dir)
        scripts = self._package_scripts(target_dir)
        report = VerificationReport(project_type="frontend", project_dir=str(target_dir))

        install = self.runner(["npm", "install"], target_dir, self.command_timeout)
        report.commands.append(install)
        report.install = install.status
        if install.status != "passed":
            report.typecheck = "skipped"
            report.build = "skipped"
            report.tests = "skipped"
            report.browser_result = BrowserResult(status="skipped", skipped_reason="npm install failed")
            report.browser = report.browser_result.status
            return self._write_report(report)

        typecheck = self._run_script_if_present("typecheck", scripts, target_dir)
        report.commands.append(typecheck)
        report.typecheck = typecheck.status

        build = self._run_script_if_present("build", scripts, target_dir)
        report.commands.append(build)
        report.build = build.status

        tests = self._run_script_if_present("test", scripts, target_dir)
        report.commands.append(tests)
        report.tests = tests.status

        if build.status != "passed":
            report.browser_result = BrowserResult(status="skipped", skipped_reason="build did not pass")
        else:
            report.browser_result = self.verify_browser(target_dir)
        report.browser = report.browser_result.status
        report.console_errors = list(report.browser_result.console_errors)
        report.screenshot = report.browser_result.screenshot
        return self._write_report(report)

    def verify_browser(self, project_dir: Path) -> BrowserResult:
        if self.browser_runner:
            return self.browser_runner(project_dir, self.outputs_dir)
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return BrowserResult(
                status="skipped",
                skipped_reason="playwright is not installed",
            )

        start = time.monotonic()
        port = self._free_port()
        server = subprocess.Popen(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)],
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            self._wait_for_http(url, timeout=20)
            screenshot = self.outputs_dir / "screenshot.png"
            console_errors: list[str] = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.goto(url, wait_until="networkidle")
                page.screenshot(path=str(screenshot), full_page=True)
                browser.close()
            status = "failed" if console_errors else "passed"
            return BrowserResult(
                status=status,
                url=url,
                console_errors=console_errors,
                screenshot=str(screenshot),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return BrowserResult(
                status="failed",
                url=url,
                console_errors=[str(exc)],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    def frontend_verification_plan(self, project_dir: str | Path) -> list[dict[str, Any]]:
        target_dir = self._resolve(project_dir)
        scripts = self._package_scripts(target_dir)
        return [
            {"name": "install", "command": ["npm", "install"], "required": True},
            {"name": "typecheck", "command": ["npm", "run", "typecheck"], "required": "typecheck" in scripts},
            {"name": "build", "command": ["npm", "run", "build"], "required": "build" in scripts},
            {"name": "test", "command": ["npm", "run", "test"], "required": "test" in scripts},
            {"name": "browser", "command": ["playwright", "open page"], "required": True},
        ]

    def _run_script_if_present(self, script: str, scripts: dict[str, Any], cwd: Path) -> CommandResult:
        if script not in scripts:
            return CommandResult(
                name=script,
                command=["npm", "run", script],
                status="skipped",
                skipped_reason=f"package.json has no {script} script",
            )
        return self.runner(["npm", "run", script], cwd, self.command_timeout)

    def _run_command(self, command: list[str], cwd: Path, timeout: int) -> CommandResult:
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
                name=self._command_name(command),
                command=command,
                status=status,
                returncode=completed.returncode,
                stdout=completed.stdout[-20000:],
                stderr=completed.stderr[-20000:],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                name=self._command_name(command),
                command=command,
                status="failed",
                stdout=(exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
                stderr=f"timeout after {timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    def _package_scripts(self, project_dir: Path) -> dict[str, Any]:
        package_json = project_dir / "package.json"
        if not package_json.exists():
            return {}
        return json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})

    def _write_report(self, report: VerificationReport) -> VerificationReport:
        path = self.outputs_dir / "verification-report.json"
        report.report_path = str(path)
        path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return report

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

    def _command_name(self, command: list[str]) -> str:
        if command[:2] == ["npm", "install"]:
            return "install"
        if command[:2] == ["npm", "run"] and len(command) >= 3:
            return command[2]
        return command[0]

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_for_http(self, url: str, timeout: int):
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1):
                    return
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)
        raise TimeoutError(f"server did not respond at {url}: {last_error}")
