"""What teabot-ballots shows Discord, MCP clients and its queued jobs, kept in snapshot.json.

A change to a command, a tool or a job's payload fails this test with the difference. When the change is meant,
accept it with
`uv run --package teabot-ballots pytest plugins/ballots --teabot-accept`.
"""

from pathlib import Path

from teabot.testing import SnapshotCheck

from teabot_ballots import plugin


def test_the_plugin_matches_its_snapshot(teabot_snapshot: SnapshotCheck) -> None:
    teabot_snapshot(plugin, Path(__file__).parent / "snapshot.json")
