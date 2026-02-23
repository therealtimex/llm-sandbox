# Issue #149 Analysis: Real-Time Output Streaming Callbacks

## Summary

**Issue**: [Feature Request: Add callback support for real-time stdout/stderr streaming during code execution](https://github.com/vndee/llm-sandbox/issues/149)

**Request**: Add optional `on_stdout` and `on_stderr` callback parameters to the `run()` method so users can receive output chunks in real-time during code execution, rather than only after completion.

## Current Architecture

### Output Processing Flow

The current output pipeline follows this path:

```
run() → execute_commands() → execute_command() → container_api.execute_command()
                                    ↓
                            _process_output()
                                    ↓
                    ┌───────────────┴───────────────┐
                    ↓                               ↓
        _process_non_stream_output()    _process_stream_output()
                    ↓                               ↓
            (stdout, stderr)              (stdout, stderr)
                    ↓                               ↓
                    └───────────────┬───────────────┘
                                    ↓
                            ConsoleOutput(exit_code, stdout, stderr)
```

### Backend-Specific Behavior

#### Docker (`docker.py:227-253`)

`_process_stream_output()` iterates over `(stdout_chunk, stderr_chunk)` tuples from Docker's `exec_run(stream=True, demux=True)`. Chunks are accumulated into strings via `+=` before returning. **This is the primary insertion point** — callbacks can be invoked inside the `for` loop at lines 233-243.

#### Kubernetes (`kubernetes.py:88-125`)

`KubernetesContainerAPI.execute_command()` uses the Kubernetes `stream()` API with `_preload_content=False` and processes output in a `while resp.is_open()` loop (lines 111-120), calling `resp.peek_stdout()` / `resp.read_stdout()`. Chunks are accumulated before returning. **Callbacks can be invoked inside the while loop** at lines 114-120.

#### Podman (`podman.py:27-44`)

`PodmanContainerAPI.execute_command()` delegates to `container.exec_run()` similarly to Docker. It inherits `_process_stream_output()` from `SandboxDockerSession` (via `SandboxPodmanSession` → `SandboxDockerSession`), so changes to Docker's implementation would apply here automatically.

#### Micromamba

Inherits from `SandboxDockerSession`, so same as Docker.

### Key Abstraction Layers

1. **`CommandExecutionMixin`** (`core/mixins.py:268-316`): Defines `execute_command()` which calls `_process_output()`, routing to `_process_stream_output()` or `_process_non_stream_output()` based on `self.stream`.

2. **`BaseSession`** (`core/session_base.py:433-519`): The `run()` method calls `execute_commands()` → `execute_command()`. No callback parameters exist today.

3. **`ContainerAPI` Protocol** (`core/mixins.py:20-45`): Defines the contract for `execute_command()` at the container API level. Does not pass callbacks.

## Feasibility Assessment

### Complexity: **Medium**

The feature is achievable but touches multiple layers of the architecture.

### Affected Components

| Component | File | Change Required |
|-----------|------|----------------|
| `BaseSession.run()` | `core/session_base.py:433` | Add `on_stdout`/`on_stderr` params, propagate to `execute_commands` |
| `BaseSession.execute_commands()` | `core/session_base.py:297` | Propagate callbacks to `execute_command` |
| `CommandExecutionMixin.execute_command()` | `core/mixins.py:277` | Accept and propagate callbacks to `_process_output` |
| `CommandExecutionMixin._process_output()` | `core/mixins.py:302` | Pass callbacks to stream processor |
| `SandboxDockerSession._process_stream_output()` | `docker.py:227` | Invoke callbacks per chunk |
| `KubernetesContainerAPI.execute_command()` | `kubernetes.py:88` | Invoke callbacks per chunk |
| `SandboxKubernetesSession.execute_command()` | `kubernetes.py:630` | Accept and propagate callbacks |
| `ArtifactSandboxSession.run()` | `session.py:525` | Propagate callbacks |
| `InteractiveSandboxSession.run()` | `interactive.py:226` | Consider if applicable (file-based IPC may not support streaming) |
| `PooledSandboxSession.run()` | `pool/session.py:299` | Propagate callbacks |
| `ArtifactPooledSandboxSession.run()` | `pool/session.py:511` | Propagate callbacks |
| `ConsoleOutput` | `data.py:52` | No change needed (still accumulates full output) |

### Implementation Approach

#### 1. Define Callback Type

```python
# In data.py or a new types module
from typing import Callable, Optional

StreamCallback = Callable[[str], None]
```

#### 2. Modify `_process_stream_output()` (Docker)

```python
def _process_stream_output(
    self,
    output: Any,
    on_stdout: StreamCallback | None = None,
    on_stderr: StreamCallback | None = None,
) -> tuple[str, str]:
    stdout_output, stderr_output = "", ""
    try:
        for stdout_chunk, stderr_chunk in output:
            if stdout_chunk:
                decoded = (
                    stdout_chunk.decode("utf-8", errors=self.config.encoding_errors)
                    if isinstance(stdout_chunk, bytes)
                    else str(stdout_chunk)
                )
                stdout_output += decoded
                if on_stdout:
                    on_stdout(decoded)
            if stderr_chunk:
                decoded = (
                    stderr_chunk.decode("utf-8", errors=self.config.encoding_errors)
                    if isinstance(stderr_chunk, bytes)
                    else str(stderr_chunk)
                )
                stderr_output += decoded
                if on_stderr:
                    on_stderr(decoded)
    except Exception as e:
        # ... existing error handling
    return stdout_output, stderr_output
```

#### 3. Modify Kubernetes `execute_command()`

```python
def execute_command(
    self,
    container: Any,
    command: str,
    on_stdout: StreamCallback | None = None,
    on_stderr: StreamCallback | None = None,
    **kwargs: Any,
) -> tuple[int, Any]:
    # ... existing setup ...
    while resp.is_open():
        resp.update(timeout=1)
        if resp.peek_stdout():
            chunk = resp.read_stdout()
            stdout_output += chunk
            if on_stdout:
                on_stdout(chunk)
        if resp.peek_stderr():
            chunk = resp.read_stderr()
            stderr_output += chunk
            if on_stderr:
                on_stderr(chunk)
    # ...
```

#### 4. Thread Callbacks Through the Call Chain

The callbacks need to propagate through:
- `run(on_stdout=..., on_stderr=...)`
- → `execute_commands(on_stdout=..., on_stderr=...)`
- → `execute_command(on_stdout=..., on_stderr=...)`
- → `_process_output(output, on_stdout=..., on_stderr=...)`
- → `_process_stream_output(output, on_stdout=..., on_stderr=...)`

### Design Considerations

#### 1. Stream Mode Interaction

Callbacks only make sense when `stream=True` (Docker/Podman). For non-streaming mode, the entire output is returned at once — callbacks would fire once with the complete output, defeating the purpose. The implementation should:
- **Automatically enable streaming** when callbacks are provided, OR
- **Document** that `stream=True` is required for real-time callbacks, OR
- **Raise an error** if callbacks are provided without `stream=True`

**Recommendation**: Automatically enable streaming when callbacks are provided. This provides the best user experience.

#### 2. Kubernetes Differences

Kubernetes already processes output chunk-by-chunk regardless of a `stream` flag (the K8s API always streams). Callbacks can be inserted directly into the existing while loop.

#### 3. Non-Streaming Backends

For `_process_non_stream_output()`, callbacks could still be called once with the full output for API consistency. This is harmless and ensures the callback contract is simple: "your callback may be called zero or more times."

#### 4. InteractiveSandboxSession

The interactive session uses file-based IPC (write command JSON → poll for result JSON). Real-time streaming would require a fundamentally different approach for this session type (e.g., tailing a log file). **Recommendation**: Initially exclude `InteractiveSandboxSession` from callback support and document this limitation.

#### 5. Callback Error Handling

If a user's callback raises an exception:
- It should **not** silently swallow the exception (user needs to know)
- It **should not** corrupt the accumulated output
- Consider wrapping callback invocations in try/except, logging errors, and continuing accumulation

#### 6. Thread Safety

The Docker streaming output is processed in a thread (via `_execute_with_timeout`). Callbacks will execute in that thread, not the main thread. Users should be warned about thread safety in the documentation.

#### 7. Breaking Changes

This is purely additive — new optional parameters with `None` defaults. No existing API signatures change behavior. **Zero breaking changes.**

## Risks and Edge Cases

| Risk | Severity | Mitigation |
|------|----------|------------|
| Callback exceptions disrupting execution | Medium | Wrap in try/except, continue accumulating |
| Thread-safety of user callbacks | Medium | Document that callbacks run in worker threads |
| Performance overhead from callbacks | Low | Callback overhead is negligible vs. I/O |
| Non-streaming mode confusion | Low | Auto-enable streaming when callbacks provided |
| InteractiveSandboxSession incompatibility | Low | Document limitation, exclude initially |
| Callback called with partial UTF-8 sequences | Medium | Docker's `demux=True` returns complete frames; K8s reads complete chunks. Low risk, but could add buffering if needed |

## Recommended Implementation Plan

### Phase 1: Core Callback Support
1. Define `StreamCallback` type alias
2. Add `on_stdout`/`on_stderr` to `_process_stream_output()` in Docker backend
3. Add callbacks to `KubernetesContainerAPI.execute_command()`
4. Thread callbacks through `CommandExecutionMixin.execute_command()` → `_process_output()`
5. Add callbacks to `BaseSession.run()` and `execute_commands()`

### Phase 2: Session-Level Integration
6. Update `ArtifactSandboxSession.run()` to accept and propagate callbacks
7. Update `PooledSandboxSession.run()` to propagate callbacks
8. Update `ArtifactPooledSandboxSession.run()` to propagate callbacks

### Phase 3: Testing and Documentation
9. Add unit tests for callback invocation in Docker backend
10. Add integration tests for streaming callbacks across backends
11. Document thread-safety considerations
12. Add examples in docstrings

### Phase 4: MCP Server (Optional)
13. Consider exposing streaming via MCP server (SSE or progress notifications)

## Estimated Scope

- **Files modified**: ~8-10
- **New tests**: ~5-8 test cases
- **Documentation updates**: docstrings + potentially a usage guide section
- **Risk of regressions**: Low (purely additive, all new params optional with None defaults)
