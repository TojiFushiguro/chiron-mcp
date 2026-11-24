"""Checks of team questions (webapp.run_check and its queue), in-process, with a
stand-in for the answer pipeline: what a check keeps for each kind of answer, that a
check's conversation is never listed among the admin's own, that the previous check's
conversation is deleted while an admin's own conversation never is, and that a check
waits in the queue only once.

Offline: no Chiron, no server, no model.

    .venv/bin/python tests/library_checks.py

Exit code 0 means every check held.
"""

import os
import secrets
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp()
os.environ["CHIRON_MCP_STATE_DIR"] = TMP
os.environ["CHIRON_MCP_QUESTIONS_DB"] = os.path.join(TMP, "questions.sqlite")
os.environ["CHIRON_MCP_USAGE_DB"] = "off"
os.environ.setdefault("CHIRON_MCP_USERNAME", "tester")

from chiron_mcp import library as L  # noqa: E402
from chiron_mcp import webapp as W  # noqa: E402

fails: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {str(detail)[:200]}")
    if not cond:
        fails.append(label)


DS = "synthea-small"
NEXT: list[tuple] = []     # what the stand-in answers next: (answer or None, error or None)
CALLS: list[dict] = []


def stand_in(question, dataset, username, allow_superuser, thread=None, model=None, source="live"):
    """Answers like ask_stream does: a new conversation, a trace, the answer or an error,
    and the conversation registered (as a check when it is one)."""
    answer, error = NEXT.pop(0)
    tid = "check-test-" + secrets.token_hex(6)
    CALLS.append({"q": question, "dataset": dataset, "user": username, "thread": thread,
                  "source": source, "tid": tid})
    yield "thread", tid
    yield "trace_start", {"id": secrets.token_hex(16)}
    if answer:
        yield "answer", answer
    if error:
        yield "error", error
    W.THREADS.touch(tid, username, dataset, question=question, kind="check" if source == "check" else None)


W.ask_stream = stand_in
W._access_level = lambda dataset, username, allow_superuser: None if username == "nobody" else "deid"


def run(q, answer=None, error=None, user="admin"):
    NEXT.append((answer, error))
    W.run_check(q["id"], user, True)
    return L.get(q["id"])


t = L.add_team("admin", DS, "How many patients have type 2 diabetes?", expected="31", expected_level="deid")
r = run(t, "**31** patients in Synthea 300 have type 2 diabetes.")
call = CALLS[-1]
check("a check asks the team question on its dataset, as the admin, as a check, in a new conversation",
      call["q"] == t["text"] and call["dataset"] == DS and call["user"] == "admin" and call["source"] == "check"
      and call["thread"] is None, call)
check("the same main number: matched", r["check_status"] == "matched" and r["check_value"] == "31"
      and r["check_level"] == "deid" and r["check_state"] is None, r)
check("the check's answer is kept by reference only (conversation and trace ids)",
      r["check_thread"] == call["tid"] and len(r["check_trace"] or "") == 32 and "in Synthea 300" not in str(r), r)
first = call["tid"]
r = run(t, "There are **33** patients with type 2 diabetes, out of 300.")
check("another main number: changed, with the new number", r["check_status"] == "changed" and r["check_value"] == "33", r)
check("the previous check's conversation is deleted", W.THREADS.entry(first) is None and W.THREADS.entry(CALLS[-1]["tid"]))
check("a check's conversation is never listed among the admin's own",
      not any(x["thread"].startswith("check-test-") for x in W.THREADS.mine("admin", DS)))
r = run(t, "Ask could not find a count for that.")
check("no number where one is expected: changed, nothing found", r["check_status"] == "changed" and r["check_value"] is None, r)
r = run(t, None, "The model is busy.")
check("no answer: failed, with why", r["check_status"] == "failed" and r["check_note"] == "The model is busy.", r)

chart = L.add_team("admin", DS, "Chart new hypertension diagnoses per month since 2020", expected="")
r = run(chart, "Here is the chart of **412** new diagnoses, month by month.")
check("no single number expected: answered", r["check_status"] == "answered", r)
typed = L.add_team("admin", DS, "How many patients had an emergency visit in 2023?")
r = run(typed, "**44** patients had an emergency visit in 2023.")
check("the first check of a question typed in: new, its number waiting to be kept",
      r["check_status"] == "new" and r["check_value"] == "44" and r["expected"] is None, r)
before = len(CALLS)
W.run_check(t["id"], "nobody", True)
r = L.get(t["id"])
check("no access to the dataset: failed, and nothing asked", r["check_status"] == "failed"
      and "no access" in (r["check_note"] or "") and len(CALLS) == before, r)
L.set_status(chart["id"], "retired", "admin")
W.run_check(chart["id"], "admin", True)
check("a retired question is not asked", len(CALLS) == before)

# a team question made from an admin's own answer: that conversation is theirs, and stays
own = "own-conversation-" + secrets.token_hex(4)
W.THREADS.touch(own, "admin", DS, question="How many patients have asthma?")
mine = L.add_team("admin", DS, "How many patients have asthma?", expected="27", expected_level="deid",
                  trace=secrets.token_hex(16), thread=own)
r = run(mine, "**27** patients have asthma.")
check("a check never deletes the admin's own conversation the expected answer came from",
      W.THREADS.entry(own) is not None and r["check_thread"] != own and r["check_status"] == "matched", r)
check("which stays in their own list", own in {x["thread"] for x in W.THREADS.mine("admin", DS)})
W._forget_check(own)
check("and _forget_check refuses it too", W.THREADS.entry(own) is not None)

# the queue: once, then the worker runs it
NEXT.append(("**27** patients have asthma.", None))
n1 = W.CHECKS.submit([mine["id"]], "admin", True)
n2 = W.CHECKS.submit([mine["id"]], "admin", True)
check("a check waits in the queue once", n1 == 1 and n2 == 0, (n1, n2))
deadline = time.time() + 10
while time.time() < deadline and L.get(mine["id"])["check_state"]:
    time.sleep(0.05)
check("the worker runs it", L.get(mine["id"])["check_state"] is None and L.get(mine["id"])["check_status"] == "matched")
check("a retired question cannot be queued", W.CHECKS.submit([chart["id"]], "admin", True) == 0)

print()
print(f"{len(fails)} failed" if fails else "all checks held")
sys.exit(1 if fails else 0)
