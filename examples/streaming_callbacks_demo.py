# ruff: noqa: T201, D401

"""Demo: Real-time output streaming callbacks.

This example demonstrates how to use the on_stdout and on_stderr callback
parameters to receive output in real-time as code executes in the sandbox,
rather than waiting for execution to complete.

Requires Docker to be running.

Usage:
    python examples/streaming_callbacks_demo.py
"""

import sys

import docker

from llm_sandbox import SandboxSession

client = docker.DockerClient.from_env()


def basic_streaming() -> None:
    """Basic real-time stdout streaming.

    Shows the simplest use case: printing output to the terminal
    as each chunk arrives from the sandbox container.
    """
    print("=" * 60)
    print("1. Basic streaming - output appears in real-time")
    print("=" * 60)

    code = """
import time
for i in range(5):
    print(f"Processing step {i + 1}/5...")
    time.sleep(3)
print("Done!")
"""

    with SandboxSession(lang="python", verbose=False, client=client) as session:
        result = session.run(
            code,
            on_stdout=lambda chunk: print(f"  [LIVE] {chunk}", end=""),
        )

    print(f"\n  Final exit code: {result.exit_code}")
    print()


def separate_stdout_stderr() -> None:
    """Demonstrate separate stdout and stderr callbacks.

    Shows how to handle stdout and stderr streams independently,
    which is useful for separating normal output from warnings/errors.
    """
    print("=" * 60)
    print("2. Separate stdout and stderr callbacks")
    print("=" * 60)

    code = """
import sys
print("Normal output line 1")
print("Warning: something unexpected", file=sys.stderr)
print("Normal output line 2")
print("Error: something failed", file=sys.stderr)
print("Normal output line 3")
"""

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    with SandboxSession(lang="python", verbose=False, client=client) as session:
        result = session.run(
            code,
            on_stdout=lambda chunk: stdout_lines.append(chunk),
            on_stderr=lambda chunk: stderr_lines.append(chunk),
        )

    print("  Stdout chunks received:")
    for chunk in stdout_lines:
        print(f"    [OUT] {chunk!r}")

    print("  Stderr chunks received:")
    for chunk in stderr_lines:
        print(f"    [ERR] {chunk!r}")

    print(f"\n  Final accumulated stdout: {result.stdout!r}")
    print(f"  Final accumulated stderr: {result.stderr!r}")
    print()


def progress_tracking() -> None:
    """Use callbacks to track execution progress.

    Demonstrates a practical use case: extracting structured progress
    information from the output stream to update a progress indicator.
    """
    print("=" * 60)
    print("3. Progress tracking with callbacks")
    print("=" * 60)

    code = """
import time
total = 10
for i in range(total):
    time.sleep(0.3)
    progress = (i + 1) / total * 100
    print(f"PROGRESS:{progress:.0f}")
print("COMPLETE")
"""

    def on_output(chunk: str) -> None:
        """Parse progress from output and display a progress bar."""
        for line in chunk.strip().split("\n"):
            if line.startswith("PROGRESS:"):
                pct = int(line.split(":")[1])
                bar_len = 30
                filled = int(bar_len * pct / 100)
                bar = "#" * filled + "-" * (bar_len - filled)
                print(f"\r  [{bar}] {pct}%", end="", flush=True)
            elif line == "COMPLETE":
                print("\n  Execution complete!")

    with SandboxSession(lang="python", verbose=False, client=client) as session:
        result = session.run(code, on_stdout=on_output)

    print(f"  Exit code: {result.exit_code}")
    print()


def log_collection() -> None:
    """Collect output into a structured log with timestamps.

    Shows how callbacks can be used to build structured logs
    with metadata that isn't part of the raw output.
    """
    import time

    print("=" * 60)
    print("4. Log collection with timestamps")
    print("=" * 60)

    code = """
import time
print("Starting data processing...")
time.sleep(0.5)
print("Loading dataset...")
time.sleep(0.5)
print("Training model...")
time.sleep(1)
print("Evaluation complete: accuracy=0.95")
"""

    log_entries: list[dict] = []
    start_time = time.time()

    def log_stdout(chunk: str) -> None:
        elapsed = time.time() - start_time
        log_entries.append({
            "stream": "stdout",
            "time": f"{elapsed:.2f}s",
            "content": chunk.rstrip(),
        })

    def log_stderr(chunk: str) -> None:
        elapsed = time.time() - start_time
        log_entries.append({
            "stream": "stderr",
            "time": f"{elapsed:.2f}s",
            "content": chunk.rstrip(),
        })

    with SandboxSession(lang="python", verbose=False, client=client) as session:
        session.run(code, on_stdout=log_stdout, on_stderr=log_stderr)

    print("  Collected log entries:")
    for entry in log_entries:
        print(f"    [{entry['time']}] [{entry['stream']}] {entry['content']}")
    print()


def streaming_without_explicit_stream_flag() -> None:
    """Streaming is auto-enabled when callbacks are provided.

    Demonstrates that you do NOT need to set stream=True when creating
    the session. Providing on_stdout or on_stderr callbacks automatically
    enables streaming mode for that execution.
    """
    print("=" * 60)
    print("5. Auto-enabled streaming (no explicit stream=True needed)")
    print("=" * 60)

    code = """
for i in range(3):
    print(f"Line {i + 1}")
"""

    chunks: list[str] = []

    # Note: we do NOT pass stream=True to SandboxSession
    with SandboxSession(lang="python", verbose=False, client=client) as session:
        result = session.run(code, on_stdout=chunks.append)

    print(f"  Received {len(chunks)} chunk(s) via callback")
    print(f"  Final stdout: {result.stdout!r}")
    print()


if __name__ == "__main__":
    demos = [
        basic_streaming,
        separate_stdout_stderr,
        progress_tracking,
        log_collection,
        streaming_without_explicit_stream_flag,
    ]

    if len(sys.argv) > 1:
        # Run specific demo by number
        idx = int(sys.argv[1]) - 1
        if 0 <= idx < len(demos):
            demos[idx]()
        else:
            print(f"Invalid demo number. Choose 1-{len(demos)}")
    else:
        for demo in demos:
            demo()
