from __future__ import annotations

import json
import types
from pathlib import Path

from harness.context_manager import COMPRESSION_SUMMARY_KEYS, ContextManager


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


def test_model_content_blocks_are_serialized(tmp_path: Path):
    manager = ContextManager(tmp_path)
    session = manager.create_session("coder", "Store provider response")
    block = types.SimpleNamespace(type="text", text="hello")

    manager.append_message(session.session_id, "assistant", [block])

    content = manager.get_messages(session.session_id)[0]["content"]
    assert content == [{"type": "text", "text": "hello"}]


def test_compress_session_uses_fixed_summary_schema(tmp_path: Path):
    manager = ContextManager(tmp_path, max_recent_messages=1)
    session = manager.create_session("planner", "用户要生成一个可运行 React 网页")
    manager.append_message(session.session_id, "user", "build an app")
    manager.append_message(session.session_id, "assistant", "I will use Vite")

    record = manager.compress_session(
        session.session_id,
        {
            "goal": "用户要生成一个可运行 React 网页",
            "decisions": ["使用 Vite + React + TS"],
            "files_changed": ["package.json", "src/App.tsx"],
            "open_issues": ["还没跑浏览器验证"],
            "next_actions": ["npm run build", "playwright verify"],
        },
        trigger="message_threshold",
        working_memory={"current_task": "generate frontend"},
        long_term_memory={"tech_stack": ["Vite", "React", "TypeScript"]},
    )

    assert tuple(record.summary.to_dict().keys()) == COMPRESSION_SUMMARY_KEYS
    assert record.summary.decisions == ["使用 Vite + React + TS"]
    assert Path(record.transcript_path).exists()

    messages = manager.get_messages(session.session_id)
    assert messages[0]["content"]["type"] == "context_summary"
    assert tuple(messages[0]["content"]["summary"].keys()) == COMPRESSION_SUMMARY_KEYS
    assert "working_memory" not in messages[0]["content"]

    stored = json.loads((tmp_path / ".context" / "summaries" / f"{session.session_id}.compression.json").read_text())
    assert stored["working_memory"]["current_task"] == "generate frontend"
    assert stored["long_term_memory"]["tech_stack"] == ["Vite", "React", "TypeScript"]


def test_compression_prompt_names_four_layers_and_schema(tmp_path: Path):
    manager = ContextManager(tmp_path)

    prompt = manager.build_compression_prompt(
        goal="用户要生成一个可运行 React 网页",
        short_term_context=[{"role": "user", "content": "make app"}],
        working_memory={"todos": ["build"]},
        long_term_memory={"user_preferences": ["React"]},
        trigger="phase_transition",
    )

    assert "Required schema" in prompt
    assert "short_term_context" in prompt
    assert "working_memory" in prompt
    assert "long_term_memory" in prompt
    assert '"goal": "用户要生成一个可运行 React 网页"' in prompt


def test_should_compress_for_threshold_and_long_tool_result(tmp_path: Path):
    manager = ContextManager(tmp_path, max_tool_result_chars=5)

    assert manager.should_compress([{"role": "user", "content": "abcdef"}], message_char_threshold=3)
    assert manager.should_compress([
        {
            "role": "user",
            "content": [{"type": "tool_result", "content": "x" * 10}],
        }
    ])


def test_child_completion_parent_message_contains_compressed_context(tmp_path: Path):
    manager = ContextManager(tmp_path)
    parent = manager.create_session("planner", "parent")
    child = manager.create_session("coder", "child goal", parent_session_id=parent.session_id)

    manager.complete_session(
        child.session_id,
        result="done",
        decisions=["used Vite"],
        files_changed=["src/App.tsx"],
        open_issues=["needs browser check"],
        next_actions=["run verifier"],
    )

    parent_message = manager.get_messages(parent.session_id)[0]["content"]
    assert parent_message["compression_trigger"] == "child_agent_complete"
    assert parent_message["compressed_context"] == {
        "goal": "child goal",
        "decisions": ["used Vite"],
        "files_changed": ["src/App.tsx"],
        "open_issues": ["needs browser check"],
        "next_actions": ["run verifier"],
    }
