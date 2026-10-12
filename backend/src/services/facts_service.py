from __future__ import annotations

import json
import posixpath
import re
import tomllib

from ..schemas.repository_analysis import (
    CommandKind,
    CoverageReport,
    EntryPoint,
    RepositoryContext,
    RepositoryFacts,
    RunCommand,
)

MAX_COMMANDS = 30
MAX_README_COMMANDS = 12
MAX_ENTRY_POINTS = 12

_IGNORED_DIRECTORIES = {"node_modules", "vendor", "dist", "build", "coverage", ".next", ".venv", "target", "examples", "fixtures"}

# README code blocks often contain output or code; only keep lines that start like a shell command.
_COMMAND_PREFIXES = (
    "npm ", "npx ", "pnpm ", "yarn", "bun ", "deno ", "node ",
    "pip ", "pip3 ", "python ", "python3 ", "py ", "uv ", "poetry ", "pipenv ", "pytest", "uvicorn ", "flask ", "django-admin ",
    "make", "just ", "docker ", "docker-compose ", "cargo ", "go ", "mvn ", "./mvnw", "gradle ", "./gradlew",
    "bundle ", "rails ", "rake ", "dotnet ", "mix ", "composer ", "php ", "git clone ", "cd ", "cp ", "copy-item ",
    "source ", ".\\", "./", "conda ", "brew ", "terraform ", "kubectl ", "helm ",
)
_SHELL_FENCE = re.compile(r"^\s*```\s*(bash|sh|shell|console|zsh|powershell|ps1|pwsh|cmd|bat|terminal|)\s*$", re.IGNORECASE)

_SCRIPT_KINDS: list[tuple[CommandKind, tuple[str, ...]]] = [
    ("test", ("test", "e2e", "spec", "coverage")),
    ("quality", ("lint", "format", "typecheck", "type-check", "check", "prettier", "eslint")),
    ("build", ("build", "compile", "bundle", "package")),
    ("run", ("dev", "start", "serve", "watch", "preview", "run")),
    ("setup", ("install", "setup", "bootstrap", "prepare", "migrate", "seed")),
]

# File name -> why it is likely an entry point. Matching is on the final path segment.
_ENTRY_POINT_NAMES = {
    "main.py": "Python program entry point",
    "app.py": "Python application module",
    "manage.py": "Django management entry point",
    "asgi.py": "ASGI server entry point",
    "wsgi.py": "WSGI server entry point",
    "__main__.py": "Python package entry point (python -m)",
    "server.js": "Node.js server entry point",
    "server.ts": "Node.js server entry point",
    "main.ts": "TypeScript application entry point",
    "main.tsx": "React application bootstrap",
    "main.js": "JavaScript application entry point",
    "app.tsx": "Root React component",
    "app.jsx": "Root React component",
    "main.go": "Go program entry point",
    "main.rs": "Rust binary entry point",
    "lib.rs": "Rust library root",
    "program.cs": ".NET program entry point",
    "application.java": "Java application entry point",
    "dockerfile": "Defines how the app is built and started in a container",
    "docker-compose.yml": "Defines the local multi-service stack",
    "docker-compose.yaml": "Defines the local multi-service stack",
    "compose.yml": "Defines the local multi-service stack",
    "compose.yaml": "Defines the local multi-service stack",
}
_NEXT_ENTRY_POINTS = {
    "app/page.tsx": "Next.js home page (App Router)",
    "app/page.jsx": "Next.js home page (App Router)",
    "app/page.js": "Next.js home page (App Router)",
    "app/layout.tsx": "Next.js root layout (App Router)",
    "pages/index.tsx": "Next.js home page (Pages Router)",
    "pages/index.jsx": "Next.js home page (Pages Router)",
    "pages/index.js": "Next.js home page (Pages Router)",
    "pages/_app.tsx": "Next.js app wrapper (Pages Router)",
}


def build_repository_facts(context: RepositoryContext) -> RepositoryFacts:
    return RepositoryFacts(
        commands=extract_commands(context),
        entry_points=detect_entry_points(context),
        coverage=CoverageReport(
            readme_found=bool(context.readme),
            readme_truncated=context.readme_truncated,
            tree_entries_inspected=len(context.file_tree),
            tree_entries_total=context.file_tree_total,
            tree_truncated=context.file_tree_truncated,
            source_files_inspected=[source.path for source in context.source_files],
            source_files_skipped=context.source_files_skipped,
        ),
    )


def extract_commands(context: RepositoryContext) -> list[RunCommand]:
    tree_paths = {entry.path for entry in context.file_tree}
    order = {"setup": 0, "run": 1, "test": 2, "build": 3, "quality": 4, "other": 5}
    commands: list[RunCommand] = []

    for source in context.source_files:
        name = posixpath.basename(source.path).lower()
        directory = posixpath.dirname(source.path) or "."
        file_commands: list[RunCommand] = []
        if name == "package.json":
            file_commands = _package_json_commands(source.path, source.content, directory, tree_paths)
        elif name in {"makefile", "justfile"}:
            file_commands = _make_targets(source.path, source.content, directory, "just" if name == "justfile" else "make")
        elif name == "pyproject.toml":
            file_commands = _pyproject_commands(source.path, source.content, directory, tree_paths)
        elif name == "requirements.txt":
            file_commands = [
                RunCommand(
                    kind="setup",
                    command="pip install -r requirements.txt",
                    working_directory=directory,
                    source_path=source.path,
                    line=1,
                    inferred=True,
                )
            ]
        commands.extend(sorted(file_commands, key=lambda command: order[command.kind]))

    # README steps stay in their written order because they often depend on each other (cd, then run).
    if context.readme:
        commands.extend(_readme_commands(context.readme_path, context.readme))
    return commands[:MAX_COMMANDS]


def detect_entry_points(context: RepositoryContext) -> list[EntryPoint]:
    found: dict[str, str] = {}
    for entry in context.file_tree:
        if entry.entry_type != "blob" or _is_ignored(entry.path):
            continue
        lowered = entry.path.lower()
        for suffix, reason in _NEXT_ENTRY_POINTS.items():
            if lowered == suffix or lowered.endswith(f"/{suffix}"):
                found.setdefault(entry.path, reason)
        name = posixpath.basename(lowered)
        if name in _ENTRY_POINT_NAMES and lowered.count("/") <= 3:
            found.setdefault(entry.path, _ENTRY_POINT_NAMES[name])
        if name in {"index.ts", "index.js", "index.tsx"} and lowered.count("/") <= 1:
            found.setdefault(entry.path, "Top-level module index")

    tree_paths = {entry.path for entry in context.file_tree}
    for source in context.source_files:
        if posixpath.basename(source.path).lower() != "package.json":
            continue
        manifest = _load_json(source.content)
        directory = posixpath.dirname(source.path)
        targets: list[tuple[str, str]] = []
        if isinstance(manifest.get("main"), str):
            targets.append((manifest["main"], "Declared as \"main\" in package.json"))
        binaries = manifest.get("bin")
        if isinstance(binaries, str):
            targets.append((binaries, "Declared as a CLI binary in package.json"))
        elif isinstance(binaries, dict):
            targets.extend((target, f"CLI command \"{name}\" in package.json") for name, target in binaries.items() if isinstance(target, str))
        for target, reason in targets:
            path = posixpath.normpath(posixpath.join(directory, target))
            if path in tree_paths:
                found[path] = reason

    ordered = sorted(found.items(), key=lambda item: (item[0].count("/"), item[0]))
    return [EntryPoint(path=path, reason=reason) for path, reason in ordered[:MAX_ENTRY_POINTS]]


def _package_json_commands(path: str, content: str, directory: str, tree_paths: set[str]) -> list[RunCommand]:
    manifest = _load_json(content)
    scripts = manifest.get("scripts")
    lockfiles = {
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
        "bun.lockb": "bun",
        "bun.lock": "bun",
        "package-lock.json": "npm",
    }
    manager = "npm"
    install_source = None
    for lockfile, candidate in lockfiles.items():
        lock_path = lockfile if directory == "." else f"{directory}/{lockfile}"
        if lock_path in tree_paths:
            manager, install_source = candidate, lock_path
            break

    commands: list[RunCommand] = []
    if install_source:
        install = "npm ci" if manager == "npm" else f"{manager} install"
        commands.append(
            RunCommand(kind="setup", command=install, working_directory=directory, source_path=install_source, line=1, inferred=True)
        )

    if not isinstance(scripts, dict):
        return commands
    for name, body in scripts.items():
        if not isinstance(name, str) or not isinstance(body, str) or name.startswith(("pre", "post")):
            continue
        if manager == "npm" and name in {"start", "test"}:
            invocation = f"npm {name}"
        elif manager == "npm":
            invocation = f"npm run {name}"
        else:
            invocation = f"{manager} {name}"
        commands.append(
            RunCommand(
                kind=_classify(name),
                command=invocation,
                working_directory=directory,
                source_path=path,
                line=_line_of(content, f'"{name}"'),
            )
        )
    return commands


def _make_targets(path: str, content: str, directory: str, tool: str) -> list[RunCommand]:
    commands: list[RunCommand] = []
    pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)")
    for line_number, line in enumerate(content.splitlines(), 1):
        match = pattern.match(line)
        if match and not match.group(1).startswith("."):
            target = match.group(1)
            commands.append(
                RunCommand(kind=_classify(target), command=f"{tool} {target}", working_directory=directory, source_path=path, line=line_number)
            )
    return commands


def _pyproject_commands(path: str, content: str, directory: str, tree_paths: set[str]) -> list[RunCommand]:
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    prefix = "" if directory == "." else f"{directory}/"

    def inferred(kind: CommandKind, command: str, source: str, needle: str) -> RunCommand:
        line = _line_of(content, needle) if source == path else 1
        return RunCommand(kind=kind, command=command, working_directory=directory, source_path=source, line=line, inferred=True)

    commands: list[RunCommand] = []
    if f"{prefix}uv.lock" in tree_paths:
        commands.append(inferred("setup", "uv sync", f"{prefix}uv.lock", ""))
    elif f"{prefix}poetry.lock" in tree_paths or "poetry" in tool:
        commands.append(inferred("setup", "poetry install", path, "[tool.poetry"))
    elif "build-system" in data or "project" in data:
        commands.append(inferred("setup", "pip install -e .", path, "[project"))
    if "pytest" in tool:
        commands.append(inferred("test", "pytest", path, "[tool.pytest"))
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    scripts = project.get("scripts", {})
    if isinstance(scripts, dict):
        for name in scripts:
            commands.append(RunCommand(kind="run", command=str(name), working_directory=directory, source_path=path, line=_line_of(content, str(name))))
    return commands


def _readme_commands(readme_path: str, readme: str) -> list[RunCommand]:
    commands: list[RunCommand] = []
    in_block = False
    shell_block = False
    for line_number, raw_line in enumerate(readme.splitlines(), 1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            if in_block:
                in_block = False
            else:
                in_block = True
                shell_block = bool(_SHELL_FENCE.match(raw_line))
            continue
        if not in_block or not shell_block:
            continue
        command = re.sub(r"^(\$|>|PS [^>]*>)\s*", "", stripped)
        if not command or command.startswith("#") or len(command) > 160:
            continue
        if not command.lower().startswith(_COMMAND_PREFIXES):
            continue
        commands.append(RunCommand(kind=_classify(command), command=command, source_path=readme_path, line=line_number))
        if len(commands) >= MAX_README_COMMANDS:
            break
    return commands


def _classify(text: str) -> CommandKind:
    lowered = text.lower()
    if lowered.startswith(("git clone", "cd ", "cp ", "copy-item", "source ", "python -m venv", "python3 -m venv", "py -m venv")) or ".venv" in lowered:
        return "setup"
    if re.search(r"\b(install|ci|sync|pull)\b", lowered) and not re.search(r"\b(test|build)\b", lowered):
        return "setup"
    for kind, keywords in _SCRIPT_KINDS:
        if any(re.search(rf"(^|[\s:_\-./]){re.escape(keyword)}", lowered) for keyword in keywords):
            return kind
    if lowered.startswith(("pytest", "go test", "cargo test")):
        return "test"
    if lowered.startswith(("uvicorn", "flask run", "docker compose up", "docker-compose up")):
        return "run"
    return "other"


def _line_of(content: str, needle: str) -> int:
    index = content.find(needle)
    return content.count("\n", 0, index) + 1 if index >= 0 else 1


def _load_json(content: str) -> dict:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_ignored(path: str) -> bool:
    return any(part in _IGNORED_DIRECTORIES for part in path.lower().split("/"))
