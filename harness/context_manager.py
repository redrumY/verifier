"""Context isolation and summary handoff for multi-agent work.

ModelGateway decides which model a role uses. ContextManager decides what that
role is allowed to see. Child agents get task packs and private transcripts;
parents only receive structured summaries unless a caller explicitly reads an
archive path.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class TaskPack:
    task_id: str
    role: str
    objective: str
    acceptance_criteria: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    parent_session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prompt(self) -> str:
        sections = [
            f"<task id=\"{self.task_id}\" role=\"{self.role}\">",
            f"<objective>{self.objective}</objective>",
        ]
        if self.acceptance_criteria:
            sections.append("<acceptance>")
            sections.extend(f"- {item}" for item in self.acceptance_criteria)
            sections.append("</acceptance>")
        if self.relevant_files:
            sections.append("<relevant_files>")
            sections.extend(f"- {item}" for item in self.relevant_files)
            sections.append("</relevant_files>")
        if self.context_refs:
            sections.append("<context_refs>")
            sections.extend(f"- {item}" for item in self.context_refs)
            sections.append("</context_refs>")
        if self.constraints:
            sections.append("<constraints>")
            sections.extend(f"- {item}" for item in self.constraints)
            sections.append("</constraints>")
        sections.append("</task>")
        return "\n".join(sections)


@dataclass
class SessionSummary:
    session_id: str
    role: str
    objective: str
    result: str
    decisions: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    transcript_path: str | None = None
    created_at: float = field(default_factory=_now)

    def to_parent_message(self) -> dict[str, Any]:
        return {
            "role": "user",
            "content": {
                "type": "agent_summary",
                "summary": asdict(self),
            },
        }


@dataclass
class AgentSessionState:
    session_id: str
    role: str
    objective: str
    parent_session_id: str | None = None
    task_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    summaries: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)


class ContextManager:
    """Durable context store with explicit parent/child summary boundaries."""

    def __init__(
        self,
        root: str | Path,
        max_recent_messages: int = 20,
        max_tool_result_chars: int = 4000,
    ):
        self.root = Path(root)
        self.dir = self.root / ".context"
        self.sessions_dir = self.dir / "sessions"
        self.transcripts_dir = self.dir / "transcripts"
        self.summaries_dir = self.dir / "summaries"
        self.max_recent_messages = max_recent_messages
        self.max_tool_result_chars = max_tool_result_chars
        for directory in (self.sessions_dir, self.transcripts_dir, self.summaries_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def create_session(
        self,
        role: str,
        objective: str,
        parent_session_id: str | None = None,
        task_id: str | None = None,
        initial_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentSessionState:
        session = AgentSessionState(
            session_id=_new_id("session"),
            role=role,
            objective=objective,
            parent_session_id=parent_session_id,
            task_id=task_id,
            messages=list(initial_messages or []),
            metadata=dict(metadata or {}),
        )
        self.save_session(session)
        return session

    def load_session(self, session_id: str) -> AgentSessionState:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")
        return AgentSessionState(**json.loads(path.read_text(encoding="utf-8")))

    def save_session(self, session: AgentSessionState):
        session.updated_at = _now()
        self._session_path(session.session_id).write_text(
            json.dumps(asdict(session), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def append_message(self, session_id: str, role: str, content: Any,
                       metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self.load_session(session_id)
        message = {
            "role": role,
            "content": self._trim_content(content),
            "metadata": metadata or {},
            "ts": _now(),
        }
        session.messages.append(message)
        self.save_session(session)
        return message

    def get_messages(self, session_id: str, recent_only: bool = False) -> list[dict[str, Any]]:
        session = self.load_session(session_id)
        if recent_only:
            return session.messages[-self.max_recent_messages:]
        return list(session.messages)

    def build_task_pack(
        self,
        role: str,
        objective: str,
        task_id: str | None = None,
        parent_session_id: str | None = None,
        acceptance_criteria: list[str] | None = None,
        relevant_files: list[str] | None = None,
        context_refs: list[str] | None = None,
        constraints: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskPack:
        return TaskPack(
            task_id=task_id or _new_id("task"),
            role=role,
            objective=objective,
            acceptance_criteria=list(acceptance_criteria or []),
            relevant_files=list(relevant_files or []),
            context_refs=list(context_refs or []),
            constraints=list(constraints or []),
            parent_session_id=parent_session_id,
            metadata=dict(metadata or {}),
        )

    def create_child_session(self, task_pack: TaskPack) -> AgentSessionState:
        return self.create_session(
            role=task_pack.role,
            objective=task_pack.objective,
            parent_session_id=task_pack.parent_session_id,
            task_id=task_pack.task_id,
            initial_messages=[{"role": "user", "content": task_pack.to_prompt()}],
            metadata={"task_pack": asdict(task_pack)},
        )

    def archive_transcript(self, session_id: str) -> Path:
        session = self.load_session(session_id)
        path = self.transcripts_dir / f"{session_id}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for message in session.messages:
                handle.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        return path

    def complete_session(
        self,
        session_id: str,
        result: str,
        decisions: list[str] | None = None,
        files_changed: list[str] | None = None,
        open_issues: list[str] | None = None,
        next_actions: list[str] | None = None,
        artifacts: list[str] | None = None,
        attach_to_parent: bool = True,
    ) -> SessionSummary:
        session = self.load_session(session_id)
        transcript = self.archive_transcript(session_id)
        summary = SessionSummary(
            session_id=session.session_id,
            role=session.role,
            objective=session.objective,
            result=result,
            decisions=list(decisions or []),
            files_changed=list(files_changed or []),
            open_issues=list(open_issues or []),
            next_actions=list(next_actions or []),
            artifacts=list(artifacts or []),
            transcript_path=str(transcript),
        )
        self._summary_path(session_id).write_text(
            json.dumps(asdict(summary), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        session.summaries.append(asdict(summary))
        self.save_session(session)

        if attach_to_parent and session.parent_session_id:
            parent = self.load_session(session.parent_session_id)
            parent.messages.append(summary.to_parent_message())
            parent.summaries.append(asdict(summary))
            self.save_session(parent)
        return summary

    def compact_session(self, session_id: str, summary_text: str | None = None) -> SessionSummary:
        session = self.load_session(session_id)
        transcript = self.archive_transcript(session_id)
        recent = session.messages[-self.max_recent_messages:]
        result = summary_text or self._deterministic_summary(session)
        summary = SessionSummary(
            session_id=session.session_id,
            role=session.role,
            objective=session.objective,
            result=result,
            transcript_path=str(transcript),
        )
        session.messages = [
            {
                "role": "user",
                "content": {
                    "type": "context_summary",
                    "summary": asdict(summary),
                },
                "metadata": {"compacted": True},
                "ts": _now(),
            },
            *recent,
        ]
        session.summaries.append(asdict(summary))
        self.save_session(session)
        self._summary_path(session_id).write_text(
            json.dumps(asdict(summary), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _summary_path(self, session_id: str) -> Path:
        return self.summaries_dir / f"{session_id}.json"

    def _trim_content(self, content: Any) -> Any:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return [self._trim_content(item) for item in content]
        if isinstance(content, dict):
            copied = dict(content)
            if copied.get("type") == "tool_result" and isinstance(copied.get("content"), str):
                text = copied["content"]
                if len(text) > self.max_tool_result_chars:
                    copied["content"] = (
                        text[:self.max_tool_result_chars]
                        + f"\n[truncated {len(text) - self.max_tool_result_chars} chars]"
                    )
            return copied
        return content

    def _deterministic_summary(self, session: AgentSessionState) -> str:
        return (
            f"Session {session.session_id} ({session.role}) compacted. "
            f"Objective: {session.objective}. "
            f"Messages archived for retrieval if needed."
        )
