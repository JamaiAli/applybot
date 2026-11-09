"""Cadence de la veille : HelloWork à chaque cycle, Indeed un cycle sur N."""

from __future__ import annotations

from applypilot.telegram_bot import _sources_for_tick


def test_hellowork_every_tick_indeed_every_n():
    every = 12  # ex. 2 h si cycle = 10 min
    # tick 0 : Indeed inclus (scrape dès le 1er passage)
    assert _sources_for_tick(0, every) == ["hellowork", "indeed"]
    # ticks intermédiaires : HelloWork seul
    for t in range(1, every):
        assert _sources_for_tick(t, every) == ["hellowork"]
    # tick N : Indeed de nouveau
    assert _sources_for_tick(every, every) == ["hellowork", "indeed"]
    assert _sources_for_tick(2 * every, every) == ["hellowork", "indeed"]


def test_every_one_always_includes_indeed():
    assert _sources_for_tick(5, 1) == ["hellowork", "indeed"]
    assert _sources_for_tick(0, 1) == ["hellowork", "indeed"]
