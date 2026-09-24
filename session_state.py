"""Block-owned, instance-scoped Claude session association (never credentials)."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID


class ClaudeError(ValueError):
    """A safe, actionable block error."""


class ClaudeCancelled(ClaudeError):
    """The owning runtime stopped the activation."""


def check_cancel(context):
    """Check the public runtime cancellation callback, including managed hosts."""
    callback = context.services.get("cancel_requested")
    if callable(callback) and callback():
        raise ClaudeCancelled("Claude Code execution cancelled.")


def session_id(value):
    """Accept only canonical UUID session identifiers, never arbitrary CLI text."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ClaudeError("Claude Code returned an invalid session identifier.") from error


class SessionFile:
    """Serialize calls across hooks/processes and atomically save bounded JSON."""

    def __init__(self, context):
        storage = context.services.get("get_block_storage_dir")
        if not callable(storage):
            raise ClaudeError("Persistent sessions require a saved blueprint instance. Disable persistence for standalone runs.")
        self.directory = Path(storage())
        self.path = self.directory / "claude_session.json"
        self.owner = hashlib.sha256(str(self.directory.resolve()).encode()).hexdigest()

    @contextmanager
    def locked(self, context, deadline):
        """Keep the lock inode stable; waits are bounded and cancellation-aware."""
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        with os.fdopen(os.open(self.directory / "claude_session.lock", flags, 0o600), "a") as lock:
            while True:
                check_cancel(context)
                if time.monotonic() >= deadline:
                    raise ClaudeError("Timed out waiting for the Claude session.")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
            try:
                yield self
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self):
        """Never silently replace corrupt state or follow a state-file symlink."""
        try:
            with os.fdopen(os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW), "r") as stream:
                text = stream.read(16385)
        except FileNotFoundError:
            return {}
        if len(text) > 16384:
            raise ClaudeError("Claude session state is too large.")
        try:
            record = json.loads(text)
            if not isinstance(record, dict) or record.get("schema_version") != 1:
                raise ValueError("schema")
            if record.get("session_id"):
                session_id(record["session_id"])
            return record
        except (ValueError, TypeError) as error:
            raise ClaudeError("Invalid Claude session state; restore the block-owned JSON file before running.") from error

    def save(self, identifier, *, directory, generation):
        """Save an association, not a transcript; an empty ID explicitly forgets it."""
        record = {"schema_version": 1, "owner": self.owner, "session_id": identifier,
                  "working_directory": str(directory), "generation": generation}
        fd, name = tempfile.mkstemp(prefix=".claude-session-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return record

    def reusable(self, record, *, directory, generation):
        """A copied instance, new working directory or reset starts its own session."""
        if (record.get("owner") == self.owner and record.get("working_directory") == str(directory)
                and record.get("generation") == generation):
            return record.get("session_id", "")
        return ""
