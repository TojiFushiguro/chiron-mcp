"""The question library (chiron_mcp/library.py): saved questions, team questions, the
suggestions people make, use counts, checks and their results, and what the Ask page
may see of each, against a throwaway SQLite file.

Offline: no Chiron, no server, no model.

    .venv/bin/python tests/library.py

Exit code 0 means every check held.
"""

import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp()
os.environ["CHIRON_MCP_QUESTIONS_DB"] = os.path.join(TMP, "questions.sqlite")
os.environ["CHIRON_MCP_USAGE_DB"] = os.path.join(TMP, "usage.sqlite")

from chiron_mcp import library as L  # noqa: E402
from chiron_mcp import usage  # noqa: E402

fails: list[str] = []


def check(label: str, cond: bool, detail="") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {str(detail)[:200]}")
    if not cond:
        fails.append(label)


def raises(fn) -> str | None:
    try:
        fn()
    except ValueError as exc:
        return str(exc)
    return None


DS, OTHER = "synthea-small", "dataset1_stored"
# What the Ask page may ever get about a question: words, and whether it is checked.
PUBLIC_KEYS = {"id", "kind", "name", "text", "dataset", "pinned", "topic", "checked", "review", "no_number",
               "uses", "last_used", "created", "suggested"}

# -- saved questions -----------------------------------------------------------------------
a = L.save_mine("ann", DS, "  How many patients   have asthma? ", "Asthma count")
check("a saved question keeps clean words and its name",
      a["text"] == "How many patients have asthma?" and a["name"] == "Asthma count" and a["kind"] == "mine", a)
again = L.save_mine("ann", DS, "how many patients have ASTHMA", "")
check("the same question again (case, spacing, the question mark) is the same saved question",
      again["id"] == a["id"] and len(L.mine("ann", DS)) == 1, again)
renamed = L.save_mine("ann", DS, "How many patients have asthma?", "Asthma")
check("saving it again with a name renames it", renamed["id"] == a["id"] and renamed["name"] == "Asthma", renamed)
check("an empty question is refused", raises(lambda: L.save_mine("ann", DS, "   ")) is not None)
check("a very long question is cut to the limit", len(L.save_mine("ann", DS, "x" * 5000)["text"]) == L.MAX_TEXT)
b = L.save_mine("ann", DS, "How many patients have hypertension?")
c = L.save_mine("ann", OTHER, "How many subjects are there?")
L.save_mine("bob", DS, "Bob's own question about diabetes")
check("each person sees only their own", all(q["owner"] == "ann" for q in L.mine("ann")) and
      not any(q["owner"] == "ann" for q in L.mine("bob")))
check("a dataset's own questions only", {q["id"] for q in L.mine("ann", OTHER)} == {c["id"]})
L.record_use(b["id"], "ann")
L.record_use(b["id"], "ann")
order = [q["id"] for q in L.mine("ann", DS)]
check("the questions asked most come first", order[0] == b["id"], order)
check("use counts: times and people", L.uses([b["id"]])[b["id"]]["uses"] == 2 and
      L.uses([b["id"]])[b["id"]]["people"] == 1)
old = L.MAX_MINE
L.MAX_MINE = 3
check("a person's saved questions per dataset are capped", "Delete some first" in (raises(
    lambda: L.save_mine("ann", DS, "one more question")) or ""))
L.MAX_MINE = old

# -- team questions --------------------------------------------------------------------------
t1 = L.add_team("admin", DS, "How many patients have type 2 diabetes?", "T2D count", pinned=True,
                expected="686", expected_level="deid", trace="a" * 32, thread="t-1")
p = L.public(t1)
check("a team question made from an answer starts out checked", t1["check_status"] == "matched" and
      p.get("checked") and p["pinned"] is True, p)
check("the page never gets the expected answer or what a check found",
      set(p) <= PUBLIC_KEYS and "686" not in str(p), p)
t2 = L.add_team("admin", DS, "Chart new hypertension diagnoses per month since 2020", expected="")
check("no single number: answered, never a checked mark, and the page knows why",
      t2["check_status"] == "answered" and L.public(t2).get("no_number") and not L.public(t2).get("checked"),
      L.public(t2))
t3 = L.add_team("admin", DS, "How many patients had a heart attack after a diabetes diagnosis?")
check("a question typed in has no expected answer yet", t3["expected"] is None and not L.public(t3).get("checked"))
dup = L.add_team("admin", DS, "how many patients have type 2 diabetes")
check("the same team question twice is one", dup["id"] == t1["id"])
check("team questions: pinned first", L.team(DS)[0]["id"] == t1["id"])
check("another dataset's team questions are not this one's", not L.team(OTHER))
mp = L.public(L.get(a["id"]))
check("a saved question as the page gets it", set(mp) <= PUBLIC_KEYS and mp["kind"] == "mine", mp)

# -- editing ---------------------------------------------------------------------------------------
e = L.edit(t1["id"], name="Type 2 diabetes", pinned=False)
check("an admin renames and unpins", e["name"] == "Type 2 diabetes" and not e["pinned"] and e["check_status"] == "matched")
e = L.edit(t1["id"], text="How many patients have type 2 diabetes mellitus?")
check("new words clear the checked mark but keep the expected answer",
      e["check_status"] is None and e["expected"] == "686" and not L.public(e).get("checked"), e)
check("an empty question cannot replace one", raises(lambda: L.edit(t1["id"], text=" ")) is not None)
m = L.edit(a["id"], pinned=True)
check("a saved question cannot be pinned", not m["pinned"])

# -- suggestions -------------------------------------------------------------------------------------
s = L.suggest("ann", b["id"])
check("a person suggests one of their own", s["kind"] == "suggestion" and s["status"] == "open" and s["origin"] == b["id"])
check("suggesting it again makes no second suggestion", L.suggest("ann", b["id"])["id"] == s["id"] and
      len(L.suggestions(DS)) == 1)
check("nobody suggests another person's question", raises(lambda: L.suggest("bob", b["id"])) is not None)
check("the saved question shows it was suggested", next(q for q in L.mine("ann", DS) if q["id"] == b["id"])["suggested"])
check("what waits for an admin: the open suggestion", L.attention().get(DS) == 1, L.attention())
L.set_status(s["id"], "added", "admin")
check("an added suggestion no longer waits", not L.suggestions(DS) and not L.attention().get(DS))
check("a suggestion cannot be retired", raises(lambda: L.set_status(s["id"], "retired", "admin")) is not None)
check("a saved question has no status to change", raises(lambda: L.set_status(a["id"], "retired", "admin")) is not None)

# -- checks ----------------------------------------------------------------------------------------------
check("compare: the same number", L.compare("686", 686, True) == "matched")
check("compare: another number", L.compare("686", 702, True) == "changed")
check("compare: no number where one was expected", L.compare("686", None, True) == "changed")
check("compare: no single number expected", L.compare("", 12, True) == "answered")
check("compare: the first check of a question typed in", L.compare(None, 52, True) == "new")
check("compare: no answer", L.compare("686", None, False) == "failed")
check("a check is queued once", L.check_queue(t3["id"]) and not L.check_queue(t3["id"]))
check("checking() while one waits", L.checking())
L.check_running(t3["id"])
L.check_result(t3["id"], status="new", actor="admin", value="52", level="deid", trace="b" * 32, thread="t-3")
r = L.get(t3["id"])
check("a check's result is kept and it no longer waits", r["check_status"] == "new" and r["check_value"] == "52"
      and r["check_state"] is None and not L.checking(), r)
check("a first answer waits for an admin", L.attention().get(DS) == 1)
check("the page does not call a first answer checked or under review", not L.public(r).get("checked")
      and not L.public(r).get("review"))
k = L.keep(t3["id"], "admin")
check("keep: the check's number is the expected answer", k["expected"] == "52" and k["check_status"] == "matched"
      and L.public(k).get("checked"), k)
L.check_result(t3["id"], status="changed", actor="admin", value="57", level="deid", thread="t-4")
check("a changed number is under review for people", L.public(L.get(t3["id"])).get("review"))
L.check_queue(t2["id"])
check("a server restart leaves no check waiting forever", L.clear_unfinished() == 1 and
      L.get(t2["id"])["check_status"] == "failed" and not L.checking())
check("the check's conversation is known as one", L.is_check_thread("t-4") and not L.is_check_thread("t-x"))
L.set_status(t2["id"], "retired", "admin")
check("a retired question is not checked", not L.check_queue(t2["id"]))
check("a retired question leaves the page's list", t2["id"] not in {q["id"] for q in L.team(DS)} and
      t2["id"] in {q["id"] for q in L.team(DS, retired=True)})
nothing = L.add_team("admin", DS, "Patients by sex")
check("nothing to keep before a check", raises(lambda: L.keep(nothing["id"], "admin")) is not None)

# -- words that may point at one person ------------------------------------------------------------------
for text, hit in [("How many patients have type 2 diabetes?", False),
                  ("Patients with more than 1,000,000 dollars of claims", False),
                  ("Show the records of MRN 12345678", True),
                  ("Is patient John Smith diabetic?", True),
                  ("Visits of the person born on 1990-02-03", True),
                  ("What is the date of birth of subject 42", True),
                  ("How many patients are named Garcia", True)]:
    got = L.identifying(text)
    check(f"identifying({text!r})", bool(got) == hit, got)

# -- topics --------------------------------------------------------------------------------------------------
tq = L.add_team("admin", DS, "How many patients take metformin?", "Metformin", topic="  Medications  ")
check("a team question keeps a clean topic, and the page gets it",
      tq["topic"] == "Medications" and L.public(tq)["topic"] == "Medications", L.public(tq))
check("the same question added again with a topic it lacked takes it",
      L.add_team("admin", DS, t3["text"], topic="Conditions")["topic"] == "Conditions")
check("an admin moves a question to another topic", L.edit(tq["id"], topic="Drugs")["topic"] == "Drugs")
check("a saved question has no topic", L.edit(a["id"], topic="X")["topic"] is None and "topic" not in L.public(L.get(a["id"])))
check("a long topic is cut to the limit",
      len(L.add_team("admin", DS, "Patients by county", topic="x" * 99)["topic"]) == L.MAX_TOPIC)

# -- deleting, and test accounts ------------------------------------------------------------------------------
L.delete(b["id"])
check("deleting a question deletes its use counts", L.get(b["id"]) is None and not L.uses([b["id"]]))
n = L.delete_users(["ann", "bob"])
check("a person's own questions and suggestions go with them", n >= 4 and not L.mine("ann") and not L.mine("bob")
      and L.get(t1["id"]) is not None, n)

# -- the usage ledger: checks are marked, and the cost estimate ----------------------------------------------
NOW = time.time()
usage.record({"answer": "c" * 32, "user": "admin", "dataset": DS, "status": "ok", "cost_usd": 0.02},
             started=NOW - 9, ended=NOW, source="check")
usage.record({"answer": "d" * 32, "user": "ann", "dataset": DS, "status": "ok", "cost_usd": 0.04}, started=NOW - 9, ended=NOW)
usage.record({"answer": "e" * 32, "user": "ann", "dataset": OTHER, "status": "ok", "cost_usd": 0.10}, started=NOW - 9, ended=NOW)
usage.record({"answer": "f" * 32, "user": "ann", "dataset": DS, "status": "ok", "cost_usd": 9.0},
             started=NOW - 40 * 86400, ended=NOW - 40 * 86400)
rows = {r["id"]: r for r in usage.summary(recent=10)["recent"]}
check("a check's ledger row says it was a check", rows["c" * 32]["source"] == "check" and rows["d" * 32]["source"] == "live")
check("average cost on a dataset (the last 30 days only)", abs((usage.average_cost(DS) or 0) - 0.03) < 1e-9, usage.average_cost(DS))
check("average cost anywhere when a dataset has none", abs((usage.average_cost("empty") or 0) - 0.16 / 3) < 1e-9)

# -- a library file made before topics ----------------------------------------------------------------------
OLD = os.path.join(TMP, "old.sqlite")
con = sqlite3.connect(OLD)
con.executescript("""CREATE TABLE questions (id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner TEXT NOT NULL,
  dataset TEXT NOT NULL, name TEXT, text TEXT NOT NULL, created TEXT, updated TEXT,
  pinned INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', origin TEXT, expected TEXT,
  expected_level TEXT, expected_at TEXT, expected_by TEXT, check_status TEXT, check_value TEXT,
  check_level TEXT, check_at TEXT, check_by TEXT, check_trace TEXT, check_thread TEXT, check_note TEXT,
  check_state TEXT, handled_by TEXT, handled_at TEXT);
INSERT INTO questions (id, kind, owner, dataset, text) VALUES ('00112233445566ff', 'team', 'admin', 'ds', 'Old one');""")
con.close()
os.environ["CHIRON_MCP_QUESTIONS_DB"] = OLD
check("a library file made before topics gains the column, and its questions keep working",
      L.public(L.get("00112233445566ff")).get("topic") == "" and L.edit("00112233445566ff", topic="T")["topic"] == "T")

# -- off --------------------------------------------------------------------------------------------------------
os.environ["CHIRON_MCP_QUESTIONS_DB"] = "off"
check("CHIRON_MCP_QUESTIONS_DB=off turns the library off", not L.enabled() and L.path() is None)
try:
    L.save_mine("ann", DS, "q")
    refused = False
except RuntimeError:
    refused = True
check("and nothing can be written then", refused)

print()
print(f"{len(fails)} failed" if fails else "all checks held")
sys.exit(1 if fails else 0)
