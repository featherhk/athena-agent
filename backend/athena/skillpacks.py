"""Discover directory skills and lazily read their resources.

Frontmatter is a deliberately small YAML subset: a leading ``---`` block of
unindented ``key: value`` string scalars, blank lines and full-line comments.
Values may be plain, JSON double-quoted, or YAML single-quoted (``''`` escapes
a quote). There is no implicit typing, inline comment syntax, nesting, block
scalar, anchor, alias or tag support. Duplicate keys and control characters
are invalid. ``name`` and ``description`` are required; other scalar metadata
is retained. Resource text offsets count characters; binary offsets count bytes.
"""
import base64
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


CORE_SKILLS_DIR = Path(__file__).with_name("skills")
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_KEY = re.compile(r"[a-z][a-z0-9_-]*")
_MAX_RESOURCE_BYTES = 16 * 1024 * 1024


def _frontmatter(text):
    if not isinstance(text, str):
        raise ValueError("Skill metadata must be UTF-8 text")
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("SKILL.md must begin with a frontmatter block")
    for index, line in enumerate(lines[1:], 1):
        if line.rstrip("\r\n") == "---":
            header = "".join(lines[1:index])
            if len(header) > 16384:
                raise ValueError("Skill metadata is too large")
            return header.splitlines(), "".join(lines[index + 1:])
    raise ValueError("Skill frontmatter is not closed")


def _scalar(value):
    if not value:
        raise ValueError("Skill metadata requires nonempty string scalars")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid quoted skill metadata") from exc
        if not isinstance(parsed, str):
            raise ValueError("Skill metadata values must be strings")
    elif value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("Invalid single-quoted skill metadata")
        content = value[1:-1]
        index = 0
        while index < len(content):
            if content[index] == "'":
                if content[index:index + 2] != "''":
                    raise ValueError("Single quotes in metadata must be doubled")
                index += 2
            else:
                index += 1
        parsed = content.replace("''", "'")
    else:
        if value[0] in "[]{}&*!|>#%@`" or value in ("-", "?") or value.startswith(("- ", "? ")) or ": " in value or " #" in value:
            raise ValueError("Use quoted strings for special metadata characters")
        parsed = value
    if not parsed.strip() or any(not character.isprintable() for character in parsed):
        raise ValueError("Skill metadata must be nonempty single-line text")
    return parsed


def parse_skill_metadata(text):
    header, _ = _frontmatter(text)
    metadata = {}
    for line in header:
        if not line.strip() or line.startswith("#"):
            continue
        if line[0].isspace() or ":" not in line:
            raise ValueError("Only unindented scalar skill metadata is supported")
        key, value = line.split(":", 1)
        if not _KEY.fullmatch(key) or key in metadata:
            raise ValueError("Invalid or duplicate skill metadata key")
        metadata[key] = _scalar(value.strip())
    if not _NAME.fullmatch(metadata.get("name", "")):
        raise ValueError("Skill name must use lowercase letters, digits, underscores or hyphens")
    if not 1 <= len(metadata.get("description", "")) <= 300:
        raise ValueError("Skill description must contain 1 to 300 characters")
    return metadata


def _package(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Invalid skill name")
    root = CORE_SKILLS_DIR.resolve()
    package = (root / name).resolve()
    if not package.is_relative_to(root) or package == root or not package.is_dir():
        raise ValueError("Unknown or unsafe skill package")
    return package


def _resource(name, path):
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError("Skill resources require a relative POSIX path")
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive or any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError("Skill resources cannot escape their package")
    package = _package(name)
    resolved = package.joinpath(*path.split("/")).resolve()
    if not resolved.is_relative_to(package) or not resolved.is_file():
        raise ValueError("Unknown or unsafe skill resource")
    return resolved


def _read_bytes(name, path):
    with _resource(name, path).open("rb") as resource:
        data = resource.read(_MAX_RESOURCE_BYTES + 1)
    if len(data) > _MAX_RESOURCE_BYTES:
        raise ValueError("Skill resources must be at most 16 MiB")
    return data


def skill_catalog():
    catalog = []
    for directory in sorted(CORE_SKILLS_DIR.iterdir(), key=lambda entry: entry.name):
        if not directory.is_dir() or not (directory / "SKILL.md").exists():
            continue
        metadata = parse_skill_metadata(_read_bytes(directory.name, "SKILL.md").decode("utf-8"))
        if metadata["name"] != directory.name:
            raise ValueError("Skill metadata name must match its package directory")
        catalog.append(metadata)
    return sorted(catalog, key=lambda metadata: metadata["name"])


def read_skill(name):
    text = _read_bytes(name, "SKILL.md").decode("utf-8")
    if parse_skill_metadata(text)["name"] != name:
        raise ValueError("Skill metadata name must match its package directory")
    return _frontmatter(text)[1].lstrip("\r\n")


def resource_reader(name):
    """Progressive discovery belongs to a loaded skill, not the stable prompt."""
    return {"read_resource": {"op": "skill", "args": {"name": name, "path": "SKILL.md"}},
            "resource_help": "Call runtime with read_resource; set args.path to the linked relative file. Optional offset/limit page its contents. Resources are read through this tool, not searched for in the operating system."}


def read_skill_resource(name, path="SKILL.md", offset=0, limit=12000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20000:
        raise ValueError("Invalid skill resource page")
    # Validate the package identity even when requesting a supporting resource.
    read_skill(name)
    data = _read_bytes(name, path)
    result = {"name": name, "path": path, "offset": offset}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {**result, "encoding": "base64", "content": base64.b64encode(data[offset:offset + limit]).decode("ascii"),
                "total_bytes": len(data), "next_offset": min(offset + limit, len(data))}
    return {**result, "encoding": "utf-8", "content": text[offset:offset + limit],
            "total_chars": len(text), "next_offset": min(offset + limit, len(text))}
