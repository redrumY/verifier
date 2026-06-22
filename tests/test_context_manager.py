from __future__ import annotations

import json
from pathlib import Path

from harness.context_manager import ContextManager


def test_child_session_context_is_private_until_summary(tmp_path: Path):
    manager = ContextManager(tmp_path)
    parent = manager.create_session("planner", "Build a frontend app")
    manager.append_message(parent.session_id, "user", "top level request")

    task_pack = manager.build_task_pack(
        role="coder",
        objective="Implement App.tsx",
        parent_session_id=parent.session_id,
        acceptance_criteria=["npm run build passes"],
    )
    child = manager.create_child_session(task_pack)
    manager.append_message(child.session_id, "assistant", "private implementation detail")

    parent_before = manager.get_messages(parent.session_id)
    assert "private implementation detail" not in json.dumps(parent_before)

    summary = manager.complete_session(
        child.session_id,
        result="Implemented App.tsx",
        files_changed=["src/App.tsx"],
        next_actions=["Run verifier"],
    )

    parent_after = manager.get_messages(parent.session_id)
    serialized = json.dumps(parent_after)
    assert "Implemented App.tsx" in serialized
    assert "private implementation detail" not in serialized
    assert summary.transcript_path is not None
    assert Path(summary.transcript_path).exists()


def test_task_pack_prompt_contains_acceptance_and_refs(tmp_path: Path):
    manager = ContextManager(tmp_path)
    task_pack = manager.build_task_pack(
        role="tester",
        objective="Verify generated frontend",
        task_id="task_frontend_verify",
        acceptance_criteria=["No console errors", "Desktop and mobile render"],
        relevant_files=["src/App.tsx", "package.json"],
        context_refs=[".context/summaries/session_abc.json"],
        constraints=["Do not modify source files"],
    )

    prompt = task_pack.to_prompt()

    assert 'id="task_frontend_verify"' in prompt
    assert "<objective>Verify generated frontend</objective>" in prompt
    assert "- No console errors" in prompt
    assert "- src/App.tsx" in prompt
    assert "- .context/summaries/session_abc.json" in prompt
    assert "- Do not modify source files" in prompt


def test_archive_transcript_writes_jsonl(tmp_path: Path):
    manager = ContextManager(tmp_path)
    session = manager.create_session("reviewer", "Review diff")
    manager.append_message(session.session_id, "user", "review this")
    manager.append_message(session.session_id, "assistant", "looks risky")

    archive = manager.archive_transcript(session.session_id)

    lines = archive.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["content"] == "review this"
    assert json.loads(lines[1])["content"] == "looks risky"


def test_compact_session_keeps_summary_plus_recent_messages(tmp_path: Path):
    manager = ContextManager(tmp_path, max_recent_messages=2)
    session = manager.create_session("summarizer", "Compress context")
    for index in range(5):
        manager.append_message(session.session_id, "user", f"message {index}")

    summary = manager.compact_session(session.session_id, "Important facts only")
    messages = manager.get_messages(session.session_id)

    assert summary.result == "Important facts only"
    assert messages[0]["content"]["type"] == "context_summary"
    assert [m["content"] for m in messages[1:]] == ["message 3", "message 4"]
    assert Path(summary.transcript_path).exists()


def test_tool_result_content_is_trimmed(tmp_path: Path):
    manager = ContextManager(tmp_path, max_tool_result_chars=10)
    session = manager.create_session("tester", "Run command")
    manager.append_message(
        session.session_id,
        "user",
        [{"type": "tool_result", "tool_use_id": "1", "content": "x" * 30}],
    )

    content = manager.get_messages(session.session_id)[0]["content"][0]["content"]

    assert content.startswith("x" * 10)
    assert "truncated 20 chars" in content
