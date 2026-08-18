# CLI Composition Specification (Hierarchical + Dependency Injection)

## Status

Draft for implementation in `applied_motion` and adopters that mount `applied_motion` as a subsidiary CLI. 
Intention is for the composable CLI with Festo theming to become its own module.

## Problem Statement

Current cross-package CLI inclusion pattern often hardcodes a specific import target at call time, for example:

- Parent CLI command handler imports concrete child REPL function directly.
- Parent CLI command handler constructs child session directly.
- Parent CLI command wiring is coupled to one child package path.

This is workable but fragile for long-term composition:

1. Import path change in child package breaks parent command.
2. Parent cannot switch child target REPL without code edit.
3. Tests require patching module internals instead of injecting doubles.
4. Parent command cannot express multiple child CLIs cleanly.

## Goal

Define a stable composition contract so parent CLI can include subsidiary CLI/REPL via dependency injection, not direct import coupling.

## Non-Goals

- Replacing existing `CommandGroup` tree model in `applied_motion.cli.compose.core`.
- Rewriting standalone `applied-motion` command parser behavior.
- Forcing one DI framework. Plain Python callables remain enough.

## Scope

This spec covers hierarchical composition cases like:

- Parent system CLI includes motion subtree.
- Domain CLI (for example fluid control) exposes command that launches motion REPL.
- Multi-level composition where root CLI mounts several child groups and optional sub-REPL launchers.

## Terms

- **Root CLI**: top-level command process.
- **Parent group**: command group that mounts child groups or launch commands.
- **Child group**: mounted `CommandGroup` from subsidiary package.
- **Sub-REPL launcher**: callable that starts interactive subsidiary loop.
- **Launch context**: typed object carrying dependencies needed by launcher.

## Existing Building Blocks in `applied_motion`

`applied_motion` already provides composable primitives:

- `Command` and `CommandGroup` in `applied_motion.cli.compose.core`.
- Generic tree REPL driver in `applied_motion.cli.compose.repl.run_repl`.
- Argument-parser composition via `register_motion_cli(...)` in `applied_motion.cli.cli`.

This spec extends usage guidance and contracts around these primitives.

## Required Design Principles

1. **No hardcoded child imports in parent command handlers**  
   Parent receives launcher dependency from constructor/factory arguments.

2. **Typed callable contracts**  
   Launchers expose explicit input/output signatures.

3. **Late binding at composition root**  
   Root wiring code decides concrete launcher implementation.

4. **Safe defaults**  
   If launcher missing, command either:
   - hidden from registration, or
   - present but returns clear `UsageError` / `NotImplementedError`.

5. **Transportable command groups**  
   Child groups remain mountable under arbitrary namespace (`gantry`, `motion`, etc.).

## Contract: Parameterizable Sub-REPL Launcher

### Type contract

Define a protocol (or equivalent callable alias) in composition-facing API:

```python
from typing import Protocol


class SubReplLauncher(Protocol):
    def __call__(self, launch_context: object) -> int | None: ...
```

Project may specialize `launch_context` type instead of `object` where shared model exists.

### Parent registration contract

Any parent builder that adds command like `applied-motion` must accept launcher as parameter:

```python
def build_group(
    session: ParentSession,
    *,
    applied_motion_launcher: SubReplLauncher | None = None,
) -> CommandGroup:
    ...
```

Behavior:

- When launcher exists, command calls launcher with constructed context.
- When launcher is `None`, command omitted or explicitly unavailable.

### Child package adapter contract

Concrete adapter lives near integration boundary, not inside generic command handlers. Adapter owns child imports.

```python
def default_applied_motion_launcher(context: MotionLaunchContext) -> int | None:
    from applied_motion.cli.cli import run_repl
    from applied_motion.cli.session import MotionSession

    return run_repl(MotionSession(context.gantry), context.gantry)
```

This confines import fragility to one adapter function.

## Reference Composition Pattern

### 1) Define launch context model

Context includes only dependencies child launcher needs (for example `gantry`, logger, feature flags).

### 2) Inject launcher into parent group builder

Wiring happens once at top-level CLI assembly.

### 3) Add leaf command that delegates to launcher

Command handler validates prerequisites, builds context, calls injected launcher.

### 4) Mount parent group under root as usual

Uses existing `CommandGroup.add_child(...)` semantics.

## Error and Exit-Code Semantics

- Launcher should return `int | None`; `None` treated as success (`0`) by parent if parent tracks exit status.
- Parent command must convert missing prerequisites into `UsageError` with actionable message.
- Unexpected launcher exceptions bubble to existing root error boundary and logging policy.

## Test Requirements

For each parent command exposing injected sub-REPL:

1. **Delegation test**: injected launcher called once with expected context.
2. **Unavailable test**: when launcher `None`, command absent or deterministic error.
3. **Prerequisite test**: missing dependency (for example gantry) raises `UsageError`.
4. **Import-isolation test**: command module import does not require optional child CLI extras.

For adapter function:

5. **Adapter smoke test**: imports child module and calls child REPL function with expected objects.

## Migration Plan

### Phase 1 (non-breaking)

- Add optional launcher parameter to parent builders.
- Keep current direct launch behavior as default adapter wired at composition root.

### Phase 2

- Move direct import logic out of command handler into adapter function.
- Update tests to inject fake launcher.

### Phase 3

- Expose reusable helper utilities for multi-child composition (optional).

## Acceptance Criteria

Implementation is compliant when all are true:

1. Parent command handler has no direct import of child REPL module.
2. Child REPL target can be swapped in tests and production via injected callable.
3. Parent still composes with existing `CommandGroup` and `run_repl` flow.
4. Existing user-facing command names/usage unchanged unless explicitly versioned.
5. Optional child dependency missing does not break parent module import.

## Example Hierarchical Topology

```text
root
├── fluid
│   ├── status
│   ├── dispense
│   └── applied-motion   # delegates via injected launcher
└── motion               # optionally mounted full motion subtree
```

Both paths can coexist:

- Full `motion` subtree for direct command execution.
- Shortcut launch command under another domain group.

Each path still resolves concrete behavior through injected adapter, not hardcoded import in leaf handler.

## Security and Operational Notes

- Launch context should contain only required runtime objects.
- Do not pass raw credentials in context unless child launcher truly requires them.
- Keep adapter imports local to adapter call if optional dependencies are heavyweight or extra-gated.

## Future Extensions

- Named launcher registry (`dict[str, SubReplLauncher]`) for multiple subsidiary REPL targets.
- Feature-flagged inclusion/exclusion of child launch commands.
- Common typed context package for cross-repo CLI composition consistency.
