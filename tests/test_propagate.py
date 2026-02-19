"""Tests for propagation transforms."""

import pytest

from sync_agentic_tools.propagate import (
    apply_remove_markdown_sections_transform,
    apply_remove_xml_sections_transform,
    apply_transform,
)


class TestRemoveMarkdownSections:
    """Tests for apply_remove_markdown_sections_transform."""

    def test_removes_h3_section(self):
        content = (
            "## Parent\n"
            "\n"
            "### Keep This\n"
            "Keep content.\n"
            "\n"
            "### Remove This\n"
            "Remove content.\n"
            "\n"
            "### Also Keep\n"
            "Kept content.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["Remove This"])
        assert "### Remove This" not in result
        assert "Remove content." not in result
        assert "### Keep This" in result
        assert "### Also Keep" in result
        assert "Kept content." in result

    def test_removes_section_at_end_of_file(self):
        content = (
            "## Intro\n"
            "Some text.\n"
            "\n"
            "### Last Section\n"
            "This is the end.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["Last Section"])
        assert "### Last Section" not in result
        assert "This is the end." not in result
        assert "## Intro" in result
        assert "Some text." in result

    def test_removes_nested_children(self):
        """Removing a heading should also remove its sub-headings."""
        content = (
            "## Top\n"
            "\n"
            "### Parent Section\n"
            "Parent text.\n"
            "\n"
            "#### Child Section\n"
            "Child text.\n"
            "\n"
            "### Next Peer\n"
            "Peer text.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["Parent Section"])
        assert "### Parent Section" not in result
        assert "Parent text." not in result
        assert "#### Child Section" not in result
        assert "Child text." not in result
        assert "### Next Peer" in result
        assert "Peer text." in result

    def test_removes_child_but_keeps_parent(self):
        """Removing a sub-heading should keep the parent heading."""
        content = (
            "### Parent\n"
            "Parent text.\n"
            "\n"
            "#### Child to Remove\n"
            "Child text.\n"
            "\n"
            "#### Sibling Child\n"
            "Sibling text.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["Child to Remove"])
        assert "#### Child to Remove" not in result
        assert "Child text." not in result
        assert "### Parent" in result
        assert "Parent text." in result
        assert "#### Sibling Child" in result
        assert "Sibling text." in result

    def test_removes_multiple_sections(self):
        content = (
            "### One\n"
            "Text one.\n"
            "\n"
            "### Two\n"
            "Text two.\n"
            "\n"
            "### Three\n"
            "Text three.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["One", "Three"])
        assert "### One" not in result
        assert "Text one." not in result
        assert "### Two" in result
        assert "Text two." in result
        assert "### Three" not in result
        assert "Text three." not in result

    def test_no_match_returns_unchanged(self):
        content = "### Existing\nContent.\n"
        result = apply_remove_markdown_sections_transform(content, ["Nonexistent"])
        assert result == content

    def test_empty_sections_list(self):
        content = "### Heading\nContent.\n"
        result = apply_remove_markdown_sections_transform(content, [])
        assert result == content

    def test_higher_level_heading_terminates_section(self):
        """A ## heading should terminate a ### section."""
        content = (
            "## Top\n"
            "\n"
            "### Remove Me\n"
            "Removed.\n"
            "\n"
            "## Another Top\n"
            "Kept.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["Remove Me"])
        assert "### Remove Me" not in result
        assert "Removed." not in result
        assert "## Another Top" in result
        assert "Kept." in result

    def test_same_level_heading_terminates_section(self):
        content = (
            "### A\n"
            "Text A.\n"
            "\n"
            "### B\n"
            "Text B.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["A"])
        assert "### A" not in result
        assert "Text A." not in result
        assert "### B" in result
        assert "Text B." in result

    def test_realistic_claude_md(self):
        """Test with a structure resembling the real CLAUDE.md."""
        content = (
            "## Tool Usage\n"
            "\n"
            "### CLI Commands\n"
            "Use run_silent.\n"
            "\n"
            "### CLAUDE.md Features\n"
            "- Use relevant skills\n"
            "- Use tasks/TODOs\n"
            "\n"
            "#### Sub-agent Coordination\n"
            "- Define clear boundaries\n"
            "- Set explicit success criteria\n"
            "\n"
            "## Diagramming\n"
            "\n"
            "### Mermaid\n"
            "Mermaid instructions.\n"
        )
        result = apply_remove_markdown_sections_transform(
            content, ["CLAUDE.md Features", "Sub-agent Coordination"]
        )
        assert "### CLAUDE.md Features" not in result
        assert "Use relevant skills" not in result
        assert "#### Sub-agent Coordination" not in result
        assert "Define clear boundaries" not in result
        assert "## Tool Usage" in result
        assert "### CLI Commands" in result
        assert "Use run_silent." in result
        assert "## Diagramming" in result
        assert "### Mermaid" in result

    def test_section_with_code_block(self):
        content = (
            "### Keep\n"
            "Kept.\n"
            "\n"
            "### Remove\n"
            "Some text.\n"
            "```python\n"
            "# heading-like content\n"
            "print('hello')\n"
            "```\n"
            "\n"
            "### After\n"
            "After text.\n"
        )
        result = apply_remove_markdown_sections_transform(content, ["Remove"])
        assert "### Remove" not in result
        assert "print('hello')" not in result
        assert "### Keep" in result
        assert "### After" in result


class TestRemoveXmlSections:
    """Tests for apply_remove_xml_sections_transform."""

    def test_removes_paired_tags(self):
        content = "Before.\n<SECTION>Content.</SECTION>\nAfter."
        result = apply_remove_xml_sections_transform(content, ["SECTION"])
        assert "Content." not in result
        assert "Before." in result
        assert "After." in result

    def test_removes_self_closing_tags(self):
        content = "Before.\n<SECTION/>\nAfter."
        result = apply_remove_xml_sections_transform(content, ["SECTION"])
        assert "<SECTION/>" not in result
        assert "Before." in result
        assert "After." in result


class TestApplyTransform:
    """Tests for apply_transform dispatcher."""

    def test_remove_markdown_sections_type(self):
        content = "### Remove\nText.\n\n### Keep\nKept.\n"
        result = apply_transform(
            content,
            {"type": "remove_markdown_sections", "sections": ["Remove"]},
        )
        assert "### Remove" not in result
        assert "### Keep" in result

    def test_remove_markdown_sections_missing_sections(self):
        with pytest.raises(ValueError, match="requires 'sections' parameter"):
            apply_transform("content", {"type": "remove_markdown_sections"})

    def test_remove_xml_sections_type(self):
        content = "<SECTION>Content.</SECTION> After."
        result = apply_transform(
            content,
            {"type": "remove_xml_sections", "sections": ["SECTION"]},
        )
        assert "Content." not in result

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown transform type"):
            apply_transform("content", {"type": "bogus"})
