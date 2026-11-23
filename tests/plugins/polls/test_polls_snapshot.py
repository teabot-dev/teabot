"""What polls shows Discord, MCP clients and its queued jobs, kept in snapshot.json."""

from pathlib import Path

from teabot.plugins.polls import plugin
from teabot.testing import SnapshotCheck


def test_the_plugin_matches_its_snapshot(teabot_snapshot: SnapshotCheck) -> None:
    teabot_snapshot(plugin, Path(__file__).parent / "snapshot.json")
