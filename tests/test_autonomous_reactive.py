"""A *reactive* autonomy proof: the model's next action depends on what the
tools actually returned, not a fixed script.

`test_autonomous.py` plays a hard-coded sequence and ignores tool output — it
proves the tools work, not that the loop reasons over observations. Here the
fake provider inspects the latest tool result each turn and branches on it:

* it greps for the bug, and only edits the file the grep actually pointed to;
* it runs the tests, reads whether they passed, and decides whether to fix
  more or stop based on the real exit code in the observation.

Provider is still offline/deterministic, but the control flow is driven by the
genuine tool results flowing back through the agent loop.
"""

from __future__ import annotations

import re

from aio.agent import Agent
from aio.providers import AssistantTurn, ToolCall
from aio.tools import ToolContext, default_registry
from aio.web import EventUI

BUGGY = "def half(n):\n    return n / 0   # bug: division by zero\n"
TEST = "from m import half\n\ndef test_half(): assert half(10) == 5\n"


class _ReactiveProvider:
    """Decides each step from the most recent tool observation."""

    def __init__(self):
        self.fixed_path = None
        self.saw_failure = False
        self.saw_pass = False

    @staticmethod
    def _last_tool_text(messages):
        for m in reversed(messages):
            if m.role == "tool":
                return m.content
            if m.role == "assistant" and not m.tool_calls:
                break
        return None

    def chat(self, messages, tools=None, system=None):
        obs = self._last_tool_text(messages)

        # Step 1: no observation yet -> locate the bug by content search.
        if obs is None:
            return AssistantTurn(content="Searching for the bug.",
                                 tool_calls=[ToolCall("s", "grep",
                                                      {"pattern": r"/ 0", "glob": "*.py"})])

        # React to a grep hit: parse the file:line it reported, then fix THAT file.
        if self.fixed_path is None and ":" in obs and "/ 0" in obs:
            self.fixed_path = obs.split(":", 1)[0].strip()
            return AssistantTurn(
                content=f"Bug is in {self.fixed_path}; fixing it.",
                tool_calls=[ToolCall("e", "edit_file", {
                    "path": self.fixed_path,
                    "old_string": "return n / 0   # bug: division by zero",
                    "new_string": "return n / 2",
                })])

        # After editing, verify by running the tests.
        if self.fixed_path is not None and not self.saw_failure and not self.saw_pass:
            self.saw_failure = True   # mark that we've moved to verification
            return AssistantTurn(content="Running the tests.",
                                 tool_calls=[ToolCall("t", "run_shell",
                                                      {"command": "python3 -m pytest -q"})])

        # React to the test outcome in the observation: only stop if they passed.
        if obs is not None and re.search(r"\bpassed\b", obs) and "[exit code: 0]" in obs:
            self.saw_pass = True
            return AssistantTurn(content="Tests pass — fix confirmed.", tool_calls=[])

        # Observation says still failing -> we would loop again (safety stop here).
        return AssistantTurn(content="Unexpected state; stopping.", tool_calls=[])


def test_agent_reacts_to_tool_observations(tmp_path):
    (tmp_path / "m.py").write_text(BUGGY)
    (tmp_path / "test_m.py").write_text(TEST)

    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    provider = _ReactiveProvider()
    agent = Agent(provider, default_registry(), ctx, ui, "sys", max_steps=10)
    final = agent.run("a test is failing; find the bug, fix it, and verify.")

    # The fix target was DISCOVERED from the grep observation, not hard-coded.
    assert provider.fixed_path == "m.py"
    # The model only concluded success because it read a real passing result.
    assert provider.saw_pass is True
    assert "confirmed" in final.lower()

    # And the real file really got fixed + tests really pass now.
    assert "return n / 2" in (tmp_path / "m.py").read_text()
    shell = [m.content for m in agent.messages if m.role == "tool" and m.name == "run_shell"]
    assert shell and "1 passed" in shell[0] and "[exit code: 0]" in shell[0]
