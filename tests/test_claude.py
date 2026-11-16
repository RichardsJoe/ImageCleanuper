"""Claude second opinion with a fake client: only unsure photos, answers applied, spending limit."""

import json
from types import SimpleNamespace as NS

import pytest

from images import save_jpeg, scene
from imagecleanuper import claude_assist as ca
from imagecleanuper.app import Runner
from imagecleanuper.catalog import Catalog


class FakeClient:
    def __init__(self, answer="yes", cost_tokens=(1000, 50)):
        self.calls = []
        self.answer = answer
        self.cost_tokens = cost_tokens
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        checks = kw["output_config"]["format"]["schema"]["properties"]["answers"]["items"]["properties"]["check"]["enum"]
        text = json.dumps({"answers": [{"check": c, "answer": self.answer} for c in checks]})
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=text)],
                  usage=NS(input_tokens=self.cost_tokens[0], output_tokens=self.cost_tokens[1],
                           cache_creation_input_tokens=0, cache_read_input_tokens=0))


def _setup(env, n=3):
    paths, s, m1, _ = env
    for i in range(n):
        save_jpeg(scene(20 + i), m1 / f"p{i}.jpg", date=f"2018:05:0{i + 1} 10:00:00", make="Apple")
    assert Runner(paths, s).run().state == "done"
    cat = Catalog(paths.catalog)
    ids = [r[0] for r in cat.q("SELECT id FROM assets ORDER BY id")]
    with cat.tx():
        cat.db.execute("DELETE FROM predictions")
        for aid in ids:
            cat.db.execute("INSERT INTO predictions VALUES (?, 'pet', 0.5, 'review')", (aid,))
        cat.db.execute("INSERT INTO predictions VALUES (?, 'junk', 0.9, 'yes')", (ids[0],))   # confident: not asked
    s.claude_enabled = True
    return paths, s, cat, ids, str(paths.data / "derived")


def test_only_unsure_photos_are_sent_and_answers_apply(env):
    paths, s, cat, ids, derived = _setup(env)
    fc = FakeClient("yes")
    res = ca.run(cat, s, derived, client=fc)
    assert res.photos == 3 and res.answered == 3 and len(fc.calls) == 3
    call = fc.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["fallbacks"] == "default"
    assert call["messages"][0]["content"][0]["type"] == "image"
    assert {r[0] for r in cat.q("SELECT status FROM predictions WHERE check_key='pet'")} == {"yes"}
    assert cat.scalar("SELECT COUNT(*) FROM claude_opinions") == 3
    assert ca.run(cat, s, derived, client=fc).photos == 0          # never asked twice
    assert ca.spent_this_month(cat) == pytest.approx(3 * (1000 * 2 + 50 * 10) / 1e6)


def test_spending_limit_choices(env):
    paths, s, cat, ids, derived = _setup(env)
    s.claude_monthly_limit_usd = 0.004              # enough for one photo
    fc = FakeClient("no", cost_tokens=(2000, 100))  # $0.005 per photo
    asked = []
    res = ca.run(cat, s, derived, client=fc, ask_limit=lambda *a: (asked.append(a), "local")[1])
    assert res.photos == 1 and res.stopped == "limit_local" and res.left == 2
    assert asked[0][1] == pytest.approx(0.004)
    res = ca.run(cat, s, derived, client=fc, ask_limit=lambda *a: "raise")
    assert res.photos == 2 and s.claude_monthly_limit_usd == pytest.approx(5.004)
    with cat.tx():
        cat.db.execute("DELETE FROM claude_opinions")
        cat.db.execute("UPDATE predictions SET status='review' WHERE check_key='pet'")
    s.claude_monthly_limit_usd = 0.0
    with pytest.raises(ca.Paused):
        ca.run(cat, s, derived, client=fc, ask_limit=lambda *a: "pause")


def test_off_or_no_key_sends_nothing(env, monkeypatch):
    paths, s, cat, ids, derived = _setup(env, n=1)
    s.claude_enabled = False
    assert ca.run(cat, s, derived, client=FakeClient()).photos == 0
    s.claude_enabled = True
    monkeypatch.setattr(ca, "get_key", lambda: None)
    assert ca.run(cat, s, derived).stopped.startswith("error: no API key")
