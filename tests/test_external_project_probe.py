from __future__ import annotations

import json
from pathlib import Path

from eval.probe_external_project import probe_project


def test_probe_external_project_prepares_task_pack_and_sandbox(tmp_path: Path):
    project = tmp_path / "project"
    frontend = project / "frontend"
    frontend.mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({
        "scripts": {"start": "node backend/server.js"},
        "dependencies": {"express": "^4.0.0"},
    }))
    (frontend / "package.json").write_text(json.dumps({
        "proxy": "http://localhost:5000",
        "scripts": {"start": "react-scripts start", "build": "react-scripts build"},
        "dependencies": {"react": "^17.0.0", "react-scripts": "5.0.0"},
    }))
    (frontend / "src").mkdir()
    (frontend / "src" / "App.js").write_text("export default function App() { return null }")

    report, output_path = probe_project(
        project,
        run_id="probe_test",
        results_dir="data/evaluation/results",
    )

    assert output_path.exists()
    assert report["project_summary"]["frontend_stack"] == "create-react-app"
    assert report["project_summary"]["backend_stack"] == "express"
    assert report["dynamic_plan"]["metadata"]["planner_mode"] == "dynamic"
    assert report["dynamic_plan"]["metadata"]["task_type"] == "ui_api_integration"
    assert report["test_profile"]["browser"]["enabled"] is False
    assert report["task_pack"]["role"] == "coder"
    assert "Dashboard" in report["task_pack"]["metadata"]["parent_goal"]
    assert Path(report["sandbox_run"]["profile_path"]).exists()
