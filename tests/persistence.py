"""Conversations that stay, a price that moves, traces that are there.

Offline, in-process: the retention setting (unset, 0 or "never" keeps conversations for
good; a number is idle hours) and what the conversation registry does under each.

Live, against ASK_URL as the demo accounts (needs the server's CHIRON_MCP_* database
settings, like tests/ask_e2e.py):

    ask a question, then /threads lists the conversation with its title and one answer;
    a follow-up continues the same conversation and makes it two;
    /threads?thread= rebuilds both turns, each with its answer and a done trace;
    /usage.json shows one more answer after each, with a cost that is a number;
    every JSON answer says Cache-Control: no-store;
    another account sees neither the conversation nor its turns;
    /forget removes it, from /threads and from /trace.

    PERSIST_RESTART_CMD, when set, runs between the question and the follow-up (for
    example a command that restarts the Ask server); the follow-up must still continue
    the same conversation, since the registry and the transcript live on disk.

    Sharing (with the permission suite's perm_* accounts, made and removed here): a read
    share, a "can continue" share refused for different permissions and granted for
    identical ones, the colleague's follow-up in the owner's conversation, the lock while
    it is answered, who sees whose trace, a clone with the copied turns as context, and
    the re-check on open after the owner's access is reduced.

    ASK_URL=http://localhost:8900 .venv/bin/python tests/persistence.py   # --live-only, --offline-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("CHIRON_MCP_USAGE_DB", "off")   # the in-process part writes no ledger

import ask_e2e as E  # noqa: E402

fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {str(detail)[:220]}", flush=True)
    if not cond:
        fails.append(label)


# --- offline: the retention setting and the registry ------------------------------------

def offline() -> None:
    from chiron_mcp import webapp as W
    from chiron_mcp import trace as tracing

    ttl = W._ttl_hours
    check("[ttl] unset keeps conversations for good", ttl(None) is None)
    check("[ttl] 0, never and off keep them for good", all(ttl(x) is None for x in ("0", "never", "off", "  ", "NEVER")))
    check("[ttl] a number is hours", ttl("12") == 12.0 and ttl("0.5") == 0.5)
    check("[ttl] nonsense and negatives keep them for good rather than failing", ttl("abc") is None and ttl("-3") is None)

    mv = W.may_view
    check("[gate] at_least: the same level reads the same level", mv("deid", False, "deid", False, "at_least"))
    check("[gate] at_least: no identifying columns, no answer given with them", not mv("deid", True, "deid", False, "at_least"))
    check("[gate] at_least: a higher level reads a lower one", mv("deid", False, "phi", False, "at_least"))
    check("[gate] at_least: a lower level does not read a higher one", not mv("deid", False, "agg", False, "at_least"))
    check("[gate] identical: a higher level does not read a lower one", not mv("deid", False, "phi", False, "identical"))
    check("[gate] identical: the same level and right", mv("deid", True, "deid", True, "identical"))
    check("[gate] no access reads nothing", not mv("deid", False, None, False, "at_least") and not mv(None, False, "phi", True, "at_least"))

    # gated_turns: the turns of a conversation as each person may see them now, with a
    # stand-in access table and stand-in traces.
    access = {"owner": ("phi", True), "down": ("deid", False), "peer": ("deid", False), "agg": ("agg", False), "none": (None, False)}
    traces = [
        {"id": "t1", "q": "q1", "answer": "a1", "at": 1, "by": "owner", "access_level": "deid", "details": False, "trace": {"id": "t1"}},
        {"id": "t2", "q": "q2", "answer": "a2", "at": 2, "by": "owner", "access_level": "phi", "details": True, "trace": {"id": "t2"}},
        {"id": "t3", "q": "q3", "answer": "a3", "at": 3, "by": "peer", "access_level": "deid", "details": False, "trace": {"id": "t3"}},
    ]
    entry = {"user": "owner", "dataset": "ds", "writers": ["peer"], "shared": ["peer", "down", "agg"],
             "seed": [{"id": "s0", "q": "q0", "answer": "a0", "at": 0, "by": "someone", "by_name": "Someone", "access_level": "phi", "details": True}]}
    saved = (W.access_of, tracing.turns, W.SHARE_RULE, W.display_name)
    W.access_of = lambda dataset, user: access.get(user, (None, False))
    tracing.turns = lambda root, tid, users: [dict(t) for t in traces]
    W.display_name = lambda u: (u or "").title()
    try:
        def shown(viewer, rule="at_least"):
            W.SHARE_RULE = rule
            turns, withheld = W.gated_turns(entry, "tid", viewer)
            return [t.get("id") for t in turns if not t.get("withheld")], withheld
        check("[gated] the owner (phi, details) sees everything, their own turns with their traces, the peer's without",
              shown("owner") == (["s0", "t1", "t2", "t3"], 0)
              and all(("trace" in t) == (t.get("by") is None) for t in W.gated_turns(entry, "tid", "owner")[0] if not t.get("copied")))
        check("[gated] a peer at deid reads the deid turns and not the phi one, the copied phi turn held back too",
              shown("peer") == (["t1", "t3"], 2))
        check("[gated] an aggregate-only reader gets everything held back", shown("agg") == ([], 4))
        check("[gated] no access on the dataset: everything held back", shown("none") == ([], 4))
        check("[gated] under 'identical' a peer still reads the deid turns (same level and right)", shown("peer", "identical") == (["t1", "t3"], 2))
        check("[gated] under 'identical' the owner (phi) still reads their own deid turn: own turns are 'at least'",
              shown("owner", "identical") == (["s0", "t1", "t2", "t3"], 0))
        access["owner"] = ("deid", False)
        check("[gated] the owner reduced to deid loses their phi turn, keeps the deid ones", shown("owner") == (["t1", "t3"], 2))
        access["owner"] = ("phi", True)
        check("[gated] each turn carries the level it was answered at", all(t.get("access_level") for t in W.gated_turns(entry, "tid", "owner")[0] if not t.get("withheld")))
        check("[gated] a turn held back keeps its place with a note and no text",
              all(not t.get("q") and not t.get("answer") and t.get("withheld") for t in W.gated_turns(entry, "tid", "agg")[0]))
        check("[role] a writer whose permissions match the owner's is a writer; one whose differ is a reader",
              W.identical_access("ds", "owner", "peer") is False and W.identical_access("ds", "down", "peer") is True)
    finally:
        W.access_of, tracing.turns, W.SHARE_RULE, W.display_name = saved

    with tempfile.TemporaryDirectory() as tmp:
        old_state, old_ttl = W._state_dir, W.THREAD_TTL_HOURS
        W._state_dir = lambda: Path(tmp)
        try:
            W.THREAD_TTL_HOURS = None
            T = W._Threads()
            T.touch("t1", "alice", "ds", {"cohort_def": []}, question="How many patients have asthma?")
            T.touch("t1", "alice", "ds", None, question="How many of them are women?")
            T.touch("t2", "bob", "ds", None, question="Count them")
            T._data["t1"]["last"] -= 30 * 86400   # a month idle
            T._save()
            check("[registry] a month-old conversation is still valid with no retention limit", T.valid("t1", "alice", "ds"))
            mine = T.mine("alice")
            check("[registry] mine lists only alice's, with the first question as title and two answers",
                  [m["thread"] for m in mine] == ["t1"] and mine[0]["title"] == "How many patients have asthma?"
                  and mine[0]["turns"] == 2 and mine[0]["expired"] is False, mine)
            check("[registry] mine filters by dataset", T.mine("alice", "other") == [] and len(T.mine("alice", "ds")) == 1)
            T.prune()
            check("[registry] prune keeps everything with no retention limit", set(T._data) == {"t1", "t2"})
            check("[registry] turns of someone else's conversation are refused", T.turns("t1", "bob") is None)
            W.THREAD_TTL_HOURS = 24.0
            check("[registry] with a 24-hour limit the month-old one is no longer valid, and listed as expired",
                  not T.valid("t1", "alice", "ds") and T.mine("alice")[0]["expired"] is True and T.valid("t2", "bob", "ds"))
            T.prune()
            check("[registry] prune then drops it and keeps the fresh one", set(T._data) == {"t2"}, sorted(T._data))
            # The registry is read back from disk by a new server process.
            W.THREAD_TTL_HOURS = None
            T2 = W._Threads()
            check("[registry] a new server reads the registry back from disk", set(T2._data) == {"t2"} and T2._data["t2"]["title"] == "Count them")
            # Traces rebuilt into turns, oldest first, with the answer and a summary.
            root = Path(tmp) / "traces"
            tr = tracing.Trace(question="Q1", dataset="ds", user="carol", thread="t3", followup=False, prior=[])
            tr.data["answer"] = "A1"
            tr.finish(0, "")
            tr.save(root)
            time.sleep(0.01)
            tr2 = tracing.Trace(question="Q2", dataset="ds", user="carol", thread="t3", followup=True, prior=[])
            tr2.data["answer"] = "A2"
            tr2.finish(0, "")
            tr2.save(root)
            turns = tracing.turns(root, "t3", "carol")
            check("[traces] a conversation's turns come back oldest first with answers and trace ids",
                  [t["q"] for t in turns] == ["Q1", "Q2"] and [t["answer"] for t in turns] == ["A1", "A2"]
                  and all(t["trace"]["id"] and t["done"] for t in turns), turns)
            check("[traces] nobody else's", tracing.turns(root, "t3", "dave") == [])
            # A sign-on username is email-shaped; the record must still be found and owned.
            who = "jane.doe.cchmc@pcgcid.org"
            tr3 = tracing.Trace(question="Q3", dataset="ds", user=who, thread="t4", followup=False, prior=[],
                                redact=lambda t: re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", t))
            tr3.data["answer"] = "Write to jane.doe.cchmc@pcgcid.org"
            tr3.finish(0, "")
            tr3.save(root)
            got = tracing.find(root, tr3.id, who)
            check("[traces] an email-shaped owner survives redaction, while emails in the text do not",
                  bool(got) and got.get("user") == who and "[email]" in (got.get("answer") or "")
                  and tracing.turns(root, "t4", who) and tracing.find(root, tr3.id, "[email]") is None,
                  (got or {}).get("user"))
            # Records saved before the fix name "[email]" as owner: the repair gives them back.
            path = next(Path(root).glob(f"*/{tr3.id}.json"))
            broken = json.loads(path.read_text()); broken["user"] = "[email]"; path.write_text(json.dumps(broken))
            n = tracing.repair_owners(root, lambda tid: {"t4": who}.get(tid))
            check("[traces] repair_owners restores the owner from the registry",
                  n == 1 and bool(tracing.find(root, tr3.id, who)), n)
            # A conversation registered before titles were kept gets one from its first trace.
            T3 = W._Threads()
            T3._data["t3"] = {"user": "carol", "dataset": "ds", "last": time.time(), "cohort": None}
            T3._save()
            mine = T3.mine("carol")
            check("[registry] an untitled conversation takes its first traced question as title, and keeps it",
                  mine and mine[0]["title"] == "Q1" and json.loads(T3._path.read_text())["t3"].get("title") == "Q1", mine)
        finally:
            W._state_dir, W.THREAD_TTL_HOURS = old_state, old_ttl


# --- live: through the server --------------------------------------------------------------

def headers_of(path: str, cookie: str) -> dict:
    req = urllib.request.Request(E.ASK + path, headers={"Cookie": f"sessionid={cookie}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return {k.lower(): v for k, v in r.headers.items()}


def totals(cookie: str, user: str) -> dict:
    d = E.get_json(f"{E.ASK}/usage.json?period=all&recent=5&user={urllib.parse.quote(user)}", cookie)
    return d.get("totals") or {}, d.get("recent") or []


def live() -> None:
    me, other = E.mint_session("demouser"), E.mint_session("agguser")
    tag = "[live]"
    for path in ("/datasets?dataset=" + E.DS, "/threads?dataset=" + E.DS, "/usage.json?period=today", "/whoami"):
        h = headers_of(path, me)
        check(f"{tag} {path.split('?')[0]} is sent with Cache-Control: no-store", "no-store" in h.get("cache-control", ""), h.get("cache-control"))

    before, _ = totals(me, "demouser")
    ev = E.ask("How many patients have asthma?", me, E.DS)
    tid = ev.get("thread")
    trace_id = (ev.get("trace_start") or {}).get("id")
    check(f"{tag} the question was answered with a figure", bool(E.numbers(ev.get("answer") or "")) and tid, (ev.get("error") or ev.get("answer") or "")[:100])
    listed = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", me)
    mine = [t for t in listed.get("threads") or [] if t["thread"] == tid]
    check(f"{tag} /threads lists the conversation with its title and one answer",
          len(mine) == 1 and mine[0]["title"] == "How many patients have asthma?" and mine[0]["turns"] == 1 and not mine[0]["expired"], mine)
    check(f"{tag} /threads says there is no retention limit", listed.get("ttl_hours") is None, listed.get("ttl_hours"))
    after, recent = totals(me, "demouser")
    check(f"{tag} the usage total grew by one answer with a cost that is a number",
          (after.get("answers") or 0) == (before.get("answers") or 0) + 1 and isinstance(recent[0].get("cost_usd"), (int, float)),
          (before.get("answers"), after.get("answers"), recent[0].get("cost_usd") if recent else None))

    cmd = os.environ.get("PERSIST_RESTART_CMD")
    if cmd:
        print(f"  running PERSIST_RESTART_CMD: {cmd}", flush=True)
        subprocess.run(cmd, shell=True, check=False, timeout=120)
        for _ in range(30):
            try:
                E.get_json(f"{E.ASK}/health", me)
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)

    ev2 = E.ask("How many of them are women?", me, E.DS, tid)
    tr2 = E.get_json(f"{E.ASK}/trace?id={(ev2.get('trace_start') or {}).get('id')}", me)
    check(f"{tag} the follow-up continued the same conversation" + (" across the restart" if cmd else ""),
          ev2.get("thread") == tid and tr2.get("followup") is True and not tr2.get("followup_lost"),
          (ev2.get("thread") == tid, tr2.get("followup"), tr2.get("followup_lost"), (ev2.get("answer") or ev2.get("error") or "")[:80]))
    turns = E.get_json(f"{E.ASK}/threads?thread={tid}", me).get("turns") or []
    check(f"{tag} /threads?thread= rebuilds both turns with their answers and done traces",
          [t["q"] for t in turns] == ["How many patients have asthma?", "How many of them are women?"]
          and all(t.get("answer") and t["trace"]["id"] and t["done"] for t in turns), [(t.get("q"), bool(t.get("answer")), t.get("done")) for t in turns])
    listed = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", me)
    mine = [t for t in listed.get("threads") or [] if t["thread"] == tid]
    check(f"{tag} /threads now counts two answers", mine and mine[0]["turns"] == 2, mine)
    full = E.get_json(f"{E.ASK}/trace?id={trace_id}", me)
    check(f"{tag} the first answer's trace is complete", full.get("done") is True and bool(full.get("steps")), full.get("error"))
    after2, recent2 = totals(me, "demouser")
    check(f"{tag} the usage total grew again and the follow-up's cost is a number",
          (after2.get("answers") or 0) == (after.get("answers") or 0) + 1 and isinstance(recent2[0].get("cost_usd"), (int, float)),
          (after.get("answers"), after2.get("answers"), recent2[0].get("cost_usd") if recent2 else None))

    others = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", other)
    check(f"{tag} another account does not see the conversation", tid not in {t["thread"] for t in others.get("threads") or []})
    r = E.get_json(f"{E.ASK}/threads?thread={tid}", other)
    check(f"{tag} nor its turns", "error" in r and not r.get("turns"), r.get("error"))
    r = E.get_json(f"{E.ASK}/trace?id={trace_id}", other)
    check(f"{tag} nor its traces", "error" in r and not r.get("steps"), r.get("error"))

    # Sharing: the owner names a person; that person sees it under "shared", read-only,
    # with each answer only at or above the access it was answered at; the owner can undo it.
    r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "no-such-account-here"}, me)
    check(f"{tag} sharing with an unknown account is refused and says so", "error" in r and "No Chiron account" in r["error"], r)
    r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "agguser"}, me)
    check(f"{tag} /share adds a person by username", [p["username"] for p in r.get("shared_with") or []] == ["agguser"], r)
    listed = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", me)
    mine = [t for t in listed.get("threads") or [] if t["thread"] == tid]
    check(f"{tag} the owner's listing says who it is shared with", mine and [p["username"] for p in mine[0].get("shared_with") or []] == ["agguser"], mine)
    others = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", other)
    shared = [t for t in others.get("shared") or [] if t["thread"] == tid]
    check(f"{tag} the other account now lists it under shared, with the owner named",
          len(shared) == 1 and shared[0]["owner"] == "demouser" and shared[0]["title"] == "How many patients have asthma?", shared)
    r = E.get_json(f"{E.ASK}/threads?thread={tid}", other)
    shown = [t for t in r.get("turns") or [] if t.get("answer")]
    held = [t for t in r.get("turns") or [] if t.get("withheld")]
    check(f"{tag} the shared view is read-only, every turn shown or held back by access level, no trace in it",
          r.get("read_only") is True and r.get("owner") == "demouser" and len(shown) + len(held) == 2
          and r.get("withheld") == len(held) and not any("trace" in t for t in shown), (r.get("read_only"), len(shown), len(held), r.get("withheld")))
    r = E.get_json(f"{E.ASK}/trace?id={trace_id}", other)
    check(f"{tag} a shared conversation gives no access to its traces", "error" in r and not r.get("steps"), r.get("error"))
    r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "demouser"}, other)
    check(f"{tag} the viewer cannot share it on", "error" in r, r)
    r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "agguser", "remove": True}, me)
    others = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", other)
    r2 = E.get_json(f"{E.ASK}/threads?thread={tid}", other)
    check(f"{tag} unsharing takes it out of the other account's list and reach",
          r.get("shared_with") == [] and tid not in {t["thread"] for t in others.get("shared") or []} and "error" in r2, (r, r2.get("error")))

    # Reporting an answer: the owner flags it; those who see everyone's usage see the flag.
    r = E.post_json(f"{E.ASK}/flag", {"trace": trace_id, "kind": "could_not", "note": "persistence test"}, me)
    check(f"{tag} /flag reports one's own answer", r.get("flagged") is True and r.get("id"), r)
    fid = r.get("id")
    r = E.post_json(f"{E.ASK}/flag", {"trace": trace_id, "kind": "wrong"}, other)
    check(f"{tag} another account cannot flag it", "error" in r and not r.get("flagged"), r)
    admin = E.mint_session("admin")
    u = E.get_json(f"{E.ASK}/usage.json?period=all&recent=1", admin)
    flag = next((f for f in u.get("flags") or [] if f.get("id") == fid), None)
    check(f"{tag} the usage data of an admin lists the flag with the question, the answer and the note",
          flag and flag["kind"] == "could_not" and flag["question"] == "How many patients have asthma?" and flag.get("answer")
          and flag["note"] == "persistence test" and flag["status"] == "open", flag and {k: flag.get(k) for k in ("kind", "question", "status")})
    check(f"{tag} and counts the answers that said something could not be done", isinstance((u.get("totals") or {}).get("limited"), int), (u.get("totals") or {}).get("limited"))
    u2 = E.get_json(f"{E.ASK}/usage.json?period=all&recent=1", me)
    check(f"{tag} a person's own usage data carries no flags", "flags" not in u2, list(u2)[:6])
    r = E.post_json(f"{E.ASK}/usage/flag", {"id": fid, "status": "handled"}, admin)
    check(f"{tag} an admin marks it handled", r.get("ok") is True and any(f["id"] == fid and f["status"] == "handled" for f in r.get("flags") or []), r.get("ok"))

    collaboration(me, tid, trace_id)

    r = E.post_json(f"{E.ASK}/forget", {"thread": tid}, me)
    listed = E.get_json(f"{E.ASK}/threads?dataset={E.DS}", me)
    gone = tid not in {t["thread"] for t in listed.get("threads") or []}
    tr = E.get_json(f"{E.ASK}/trace?id={trace_id}", me)
    check(f"{tag} /forget removes the conversation from /threads and its traces", r.get("forgotten") is True and gone and "error" in tr, (r, gone, tr.get("error")))
    E.STARTED.discard((tid, me))


def collaboration(me: str, tid: str, trace_id: str) -> None:
    """Continuing, cloning and the re-check, on the conversation the live checks made."""
    import threading

    import permissions as PM

    tag = "[share]"
    PM.ensure_accounts()
    try:
        twin, det, agg = E.mint_session("perm_deid"), E.mint_session("perm_details"), E.mint_session("perm_agg")
        r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "perm_details", "write": True}, me)
        check(f"{tag} 'can continue' is refused for different permissions, with the reason",
              "error" in r and "identical" in r["error"], r.get("error", "")[:140])
        r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "perm_details"}, me)
        check(f"{tag} the same person can be given a read share", any(x["username"] == "perm_details" and not x["write"] and not x["identical"] for x in r.get("shared_with") or []), r)
        r = E.post_json(f"{E.ASK}/share", {"thread": tid, "with": "perm_deid", "write": True}, me)
        check(f"{tag} 'can continue' is granted for identical permissions", any(x["username"] == "perm_deid" and x["write"] and x["identical"] for x in r.get("shared_with") or []), r)
        item = next((t for t in E.get_json(f"{E.ASK}/threads?dataset={E.DS}", twin).get("shared") or [] if t["thread"] == tid), None)
        check(f"{tag} the colleague's listing says they can continue", bool(item and item.get("can_write")), item)
        v = E.get_json(f"{E.ASK}/threads?thread={tid}", twin)
        check(f"{tag} their view is writable and holds the two answers", v.get("role") == "writer" and v.get("can_write") and not v.get("read_only")
              and len([t for t in v.get("turns") or [] if t.get("answer")]) == 2, {k: v.get(k) for k in ("role", "can_write", "read_only", "withheld")})
        results: dict = {}
        th = threading.Thread(target=lambda: results.update(w=E.ask("How many of them live in Hamilton county?", twin, E.DS, tid)))
        th.start()
        time.sleep(4)
        b = E.get_json(f"{E.ASK}/threads?thread={tid}", me).get("busy")
        check(f"{tag} while they are being answered, the owner's view names who", bool(b and b.get("user") == "perm_deid"), b)
        ev_o = E.ask("How many of them are men?", me, E.DS, tid)
        check(f"{tag} the owner's question meanwhile is refused with their name, and nothing is stopped",
              "error" in ev_o and "being answered" in str(ev_o.get("error")) and not ev_o.get("answer"), (ev_o.get("error") or "")[:100])
        th.join(timeout=300)
        w = results.get("w") or {}
        check(f"{tag} the colleague's follow-up continued the conversation and was answered", w.get("thread") == tid and bool(E.numbers(w.get("answer") or "")), (w.get("error") or w.get("answer") or "")[:100])
        E.STARTED.discard((tid, twin))
        turns = E.get_json(f"{E.ASK}/threads?thread={tid}", me).get("turns") or []
        check(f"{tag} the owner sees that turn with the colleague's name and no trace of theirs",
              len(turns) == 3 and turns[-1].get("by") == "perm_deid" and turns[-1].get("by_name") and "trace" not in turns[-1] and "trace" in turns[0],
              [(t.get("by"), "trace" in t) for t in turns])
        tr = (w.get("trace_start") or {}).get("id")
        check(f"{tag} the owner cannot read the colleague's trace", "error" in E.get_json(f"{E.ASK}/trace?id={tr}", me))
        own = E.get_json(f"{E.ASK}/trace?id={tr}", twin)
        check(f"{tag} the colleague reads their own, costed as one turn", "steps" in own and isinstance((own.get("stats") or {}).get("cost_usd"), (int, float)), (own.get("stats") or {}).get("cost_usd"))

        r = E.post_json(f"{E.ASK}/clone", {"thread": tid}, det)
        cid = r.get("thread")
        check(f"{tag} a reader clones it: a conversation of their own seeded with the turns they saw", bool(cid) and r.get("turns") == 3, r)
        cv = E.get_json(f"{E.ASK}/threads?thread={cid}", det)
        check(f"{tag} the clone shows the copied turns as copied, with who asked each",
              cv.get("role") == "owner" and cv.get("cloned_from") == tid and len(cv.get("turns") or []) == 3 and all(t.get("copied") and t.get("by_name") for t in cv["turns"]), {k: cv.get(k) for k in ("role", "cloned_from")})
        ev3 = E.ask("How many of the asthma patients have COPD too?", det, E.DS, cid)
        check(f"{tag} the first question in the clone keeps its id and is answered", ev3.get("thread") == cid and bool(E.numbers(ev3.get("answer") or "")), (ev3.get("error") or ev3.get("answer") or "")[:100])
        full = E.get_json(f"{E.ASK}/trace?id={(ev3.get('trace_start') or {}).get('id')}", det)
        check(f"{tag} and carried the copied conversation as context", "copied from one that" in json.dumps(full.get("prompt") or ""))
        check(f"{tag} someone with no part in the conversation cannot clone it", "error" in E.post_json(f"{E.ASK}/clone", {"thread": tid}, agg))

        from chiron import models
        cu = models.ChironUser.objects.get(user__username="demouser", dataset__unique_id=E.DS)
        old = cu.access_level
        cu.access_level = "agg"; cu.save()
        try:
            v = E.get_json(f"{E.ASK}/threads?thread={tid}", me)
            check(f"{tag} the owner, reduced to aggregate, no longer sees their own answers", v.get("withheld") == 3 and all(t.get("withheld") for t in v.get("turns") or []), {k: v.get(k) for k in ("withheld", "role")})
            r = E.get_json(f"{E.ASK}/trace?id={trace_id}", me)
            check(f"{tag} nor their traces", "error" in r and "higher access level" in r["error"], r.get("error", "")[:80])
            item = next((t for t in E.get_json(f"{E.ASK}/threads?dataset={E.DS}", twin).get("shared") or [] if t["thread"] == tid), None)
            check(f"{tag} the colleague, no longer identical, may no longer continue", bool(item) and item.get("can_write") is False, item and item.get("can_write"))
            ev_d = E.ask("And how many are men?", me, E.DS, tid)
            check(f"{tag} the reduced owner cannot continue it either: refused, not answered, not restarted",
                  "error" in ev_d and "higher access level" in str(ev_d.get("error")) and not ev_d.get("answer") and not ev_d.get("thread"), (ev_d.get("error") or "")[:100])
            r = E.post_json(f"{E.ASK}/clone", {"thread": tid}, me)
            check(f"{tag} nor copy it (nothing of it is visible to them now)", "error" in r and "nothing to copy" in r["error"], r)
        finally:
            cu.access_level = old; cu.save()
        v = E.get_json(f"{E.ASK}/threads?thread={tid}", me)
        check(f"{tag} access restored, the answers are back", v.get("withheld") == 0, v.get("withheld"))
        E.post_json(f"{E.ASK}/forget", {"thread": cid}, det)
        for u in ("perm_details", "perm_deid"):
            E.post_json(f"{E.ASK}/share", {"thread": tid, "with": u, "remove": True}, me)
    finally:
        PM.remove_accounts()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-only", action="store_true")
    ap.add_argument("--live-only", action="store_true")
    args = ap.parse_args()
    if not args.live_only:
        offline()
    if not args.offline_only:
        live()
        E.cleanup([])
    print("ALL PASS" if not fails else f"{len(fails)} FAILED:\n  " + "\n  ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
