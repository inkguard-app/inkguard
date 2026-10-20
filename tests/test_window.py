"""The small window runs exactly the command-line tool and reports its
outcome in plain words. Its drawing needs a display; its work does not."""
import queue
from pathlib import Path

from inkguard import window

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "sample-notes"


def test_the_window_runs_the_tool_and_says_what_happened(tmp_path):
    q = queue.Queue()
    out = tmp_path / "out"
    window.run_cli(EXAMPLES, out, False, q)
    items = []
    while not q.empty():
        items.append(q.get())
    assert items[-1] == ("done", 0)
    text = window.summary(out)
    assert "flagged for review" in text and "not processed" in text and "Ready to share: 0" in text
    assert any(k == "progress" for k, _ in items)              # the bar moves file by file
    assert "A person must read every file first." in text


def test_a_refused_run_is_reported_not_raised(tmp_path):
    q = queue.Queue()
    window.run_cli(tmp_path / "no-such-folder", tmp_path / "out", False, q)
    items = []
    while not q.empty():
        items.append(q.get())
    assert items[-1][0] == "done" and items[-1][1] != 0
    assert "did not finish" in window.summary(tmp_path / "out")
