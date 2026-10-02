import base64
import json
from pathlib import Path
import pytest
from athena import skillpacks


def package(root, name, description="A useful skill", body="# Instructions\nDo useful work.\n"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n{body}")
    return directory


def test_metadata_parses_documented_scalar_subset_without_implicit_types():
    text = '''---
name: user_example
description: "A quoted \\"example\\" with \\u4e2d\\u6587"
version: 1
enabled: false
author: 'An author''s name'
# Full-line comments are supported.
---
Body does not become metadata: ignored
'''
    metadata = skillpacks.parse_skill_metadata(text)
    assert metadata == {"name": "user_example", "description": 'A quoted "example" with 中文',
                        "version": "1", "enabled": "false", "author": "An author's name"}


@pytest.mark.parametrize("frontmatter", [
    "description: No name", "name: example", "name: ../escape\ndescription: Bad name",
    "name: example\ndescription: First\ndescription: Second",
    "name: example\ndescription: |\n  Multiline",
    "name: example\ndescription: [not, a, scalar]",
    "name: example\ndescription: *alias",
    "name: example\ndescription: value # inline comment",
    "name: example\ndescription: value: nested",
    "name: example\ndescription: 'Invalid 'quotes' here'",
    "name: example\ndescription: 'Invalid triple ''' quotes'",
    'name: example\ndescription: "Line\\nbreak"',
    "name: example\ndescription: " + "a" * 301,
])
def test_metadata_rejects_unsupported_or_ambiguous_yaml(frontmatter):
    with pytest.raises(ValueError):
        skillpacks.parse_skill_metadata("---\n" + frontmatter + "\n---\nBody")


def test_metadata_requires_closed_leading_frontmatter():
    for text in ("# No metadata", "\n---\nname: example\n---", "---\nname: example"):
        with pytest.raises(ValueError):
            skillpacks.parse_skill_metadata(text)


def test_catalog_discovers_new_packages_in_stable_order_without_loading_resources(tmp_path, monkeypatch):
    second = package(tmp_path, "zeta", body="A body that is not metadata")
    first = package(tmp_path, "alpha", "First skill")
    (first / "references").mkdir()
    (first / "references" / "broken.bin").write_bytes(b"\xff\xfe")
    (tmp_path / "old.md").write_text("A flat file is not a skill package")
    monkeypatch.setattr(skillpacks, "CORE_SKILLS_DIR", tmp_path)
    assert skillpacks.skill_catalog() == [{"name": "alpha", "description": "First skill"},
                                         {"name": "zeta", "description": "A useful skill"}]
    assert skillpacks.read_skill("zeta") == "A body that is not metadata"
    assert "description:" not in skillpacks.read_skill("alpha")
    (second / "SKILL.md").write_text("---\nname: another\ndescription: Wrong identity\n---\nBody")
    with pytest.raises(ValueError, match="match"):
        skillpacks.skill_catalog()


def test_resource_reads_are_paged_and_preserve_text_and_binary(tmp_path, monkeypatch):
    directory = package(tmp_path, "sample")
    (directory / "references").mkdir()
    (directory / "references" / "guide.md").write_text("abcdef中文")
    (directory / "templates").mkdir()
    binary = b"\xff\x00\xfe\x80\x10"
    (directory / "templates" / "example.bin").write_bytes(binary)
    monkeypatch.setattr(skillpacks, "CORE_SKILLS_DIR", tmp_path)
    text = skillpacks.read_skill_resource("sample", "references/guide.md", offset=4, limit=3)
    assert text["content"] == "ef中" and text["total_chars"] == 8 and text["next_offset"] == 7
    raw = skillpacks.read_skill_resource("sample", "templates/example.bin", offset=1, limit=3)
    assert raw["encoding"] == "base64" and base64.b64decode(raw["content"]) == binary[1:4]
    assert raw["total_bytes"] == len(binary) and raw["next_offset"] == 4
    assert skillpacks.read_skill_resource("sample")["content"].startswith("---\nname: sample")


@pytest.mark.parametrize("path", ["../private.txt", "/etc/passwd", "references/../../private.txt", "./SKILL.md", "references//guide.md", "C:/private.txt", "C:\\private.txt"])
def test_resource_paths_cannot_escape_package(tmp_path, monkeypatch, path):
    package(tmp_path, "sample")
    monkeypatch.setattr(skillpacks, "CORE_SKILLS_DIR", tmp_path)
    with pytest.raises(ValueError):
        skillpacks.read_skill_resource("sample", path)


def test_symlink_escape_and_invalid_resource_pages_are_rejected(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    directory = package(root, "sample")
    outside = tmp_path / "private.txt"
    outside.write_text("Must not be read")
    (directory / "escape.txt").symlink_to(outside)
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(skillpacks, "CORE_SKILLS_DIR", root)
    with pytest.raises(ValueError):
        skillpacks.read_skill_resource("sample", "escape.txt")
    with pytest.raises(ValueError):
        skillpacks.read_skill("outside")
    for offset, limit in ((-1, 20), (0, 0), (0, 20001), (False, 20)):
        with pytest.raises(ValueError):
            skillpacks.read_skill_resource("sample", offset=offset, limit=limit)


def test_core_skill_bodies_and_references_are_on_demand():
    from athena.prompt import SKILLS, SYSTEM, read_skill
    catalog = skillpacks.skill_catalog()
    assert list(SKILLS) == sorted(entry["name"] for entry in catalog)
    assert all(entry["description"] in SYSTEM for entry in catalog)
    assert "condition-check.md" not in SYSTEM
    assert "references/condition-check.md" in read_skill("tasks")
    resource = skillpacks.read_skill_resource("tasks", "references/condition-check.md")
    assert '"trigger": target.is_file()' in resource["content"]
