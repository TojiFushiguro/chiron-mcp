"""The question library: questions people save to ask again, and the team questions an
admin picks for a dataset (shown first on Ask's new chat page).

Only words are kept, in Ask's own SQLite file and never in Chiron: a question's text
and name, its dataset, who saved it and when, and how often it was asked. A team
question also keeps its expected answer (the main number of an answer an admin looked
at, and the access level that answer was given at) and the result of its last check.
Never an answer, a result, or anything about a patient.

    CHIRON_MCP_QUESTIONS_DB    the SQLite file; unset: <state dir>/questions.sqlite;
                               "off" turns the library off (the page then hides it)

Three kinds of question:

    mine        one person's own, seen by them alone
    team        an admin's pick for a dataset, seen by everyone who may open that
                dataset; "active" or "retired"; may be pinned to the new chat page
    suggestion  a person's own question offered to the admins; "open", "added" or
                "dismissed"

A check asks a team question again (the server does it, as the admin who pressed
Check) and compares the main number of the new answer with the expected one.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_TEXT = 1000          # characters in a question
MAX_NAME = 120           # characters in its name
MAX_MINE = 300           # saved questions per person and dataset
MAX_TEAM = 200           # team questions per dataset, retired ones included

# What a check found, on a team question:
#   matched   the main number is the expected one
#   answered  no single number is expected (a list, a chart): it answered, without an error
#   changed   the main number differs from the expected one (or the answer has none now)
#   failed    no answer: an error, a refusal, the server restarted
#   new       the first check of a question typed in: an admin keeps its number, or not
NEEDS_A_LOOK = ("changed", "failed", "new")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    owner TEXT NOT NULL,
    dataset TEXT NOT NULL,
    name TEXT,
    text TEXT NOT NULL,
    created TEXT, updated TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    origin TEXT,
    expected TEXT, expected_level TEXT, expected_at TEXT, expected_by TEXT,
    check_status TEXT, check_value TEXT, check_level TEXT, check_at TEXT, check_by TEXT,
    check_trace TEXT, check_thread TEXT, check_note TEXT, check_state TEXT,
    handled_by TEXT, handled_at TEXT
);
CREATE INDEX IF NOT EXISTS questions_dataset ON questions (dataset, kind, status);
CREATE INDEX IF NOT EXISTS questions_thread ON questions (check_thread);
CREATE INDEX IF NOT EXISTS questions_owner ON questions (owner, kind);
CREATE TABLE IF NOT EXISTS question_uses (
    question TEXT NOT NULL,
    user TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS question_uses_question ON question_uses (question);
"""

_lock = threading.Lock()
_ready: set[str] = set()


# -- where it is -------------------------------------------------------------------------

def setting() -> str:
    return os.environ.get("CHIRON_MCP_QUESTIONS_DB", "").strip()


def path() -> Path | None:
    """The library's file, or None when it is turned off."""
    value = setting()
    if value.lower() in ("0", "off", "no", "none", "false"):
        return None
    if value:
        return Path(value).expanduser()
    state = Path(os.environ.get("CHIRON_MCP_STATE_DIR", Path.home() / ".cache" / "chiron-ask"))
    return state / "questions.sqlite"


def enabled() -> bool:
    return path() is not None


def reset() -> None:
    """Forget which files were prepared (tests switch files)."""
    with _lock:
        _ready.clear()


def _connect() -> sqlite3.Connection:
    p = path()
    if p is None:
        raise RuntimeError("The question library is turned off (CHIRON_MCP_QUESTIONS_DB=off).")
    con = sqlite3.connect(str(p), timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    key = str(p)
    if key not in _ready:
        with _lock:
            if key not in _ready:
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    p.parent.chmod(0o700)
                except OSError:
                    pass
                con.executescript(_SCHEMA)
                try:
                    p.chmod(0o600)
                except OSError:
                    pass
                _ready.add(key)
    return con


def _q(sql: str, args=()) -> list[dict]:
    con = _connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _x(sql: str, args=()) -> int:
    con = _connect()
    try:
        return con.execute(sql, args).rowcount
    finally:
        con.close()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return secrets.token_hex(8)


# -- the words -----------------------------------------------------------------------------

def clean_text(text) -> str:
    """A question as it is kept: one line, single spaces, at most MAX_TEXT characters."""
    return " ".join(str(text or "").split())[:MAX_TEXT]


def clean_name(name) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME]


def _norm(text) -> str:
    """For telling whether two questions are the same one: case, spacing and a closing
    question mark or full stop do not count."""
    return re.sub(r"[\s?.!]+$", "", clean_text(text)).casefold()


def valid_id(qid) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{16}", str(qid or "")))


# Team questions are read by everyone on a dataset, so a question about one person must
# not become one. What gives such a question away: a long run of digits (a record or an
# ID number; counts are shorter or carry commas), a date (a date of birth, an admission),
# the words people use for identifiers, and a capitalised name after "patient", "named"
# or a title. An admin is asked to confirm, never refused: a date can be a fair cut-off.
_ID_RUN = re.compile(r"(?<!\d)(?<!\d[,.])\d{6,}(?!\d)(?![,.]\d)")
_DATE = re.compile(r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b")
_ID_WORDS = re.compile(r"\b(mrn|medical record number|record number|ssn|social security|"
                       r"patient id|subject id|date of birth|dob|born on|address|phone number|email)\b", re.I)
_NAMED = re.compile(r"\b(?:patient|subject|named|called|mr|mrs|ms|miss|dr)\.?\s+([A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+)?)")


def identifying(text: str) -> list[str]:
    """Why a question might point at one person, in words for the admin; [] when nothing does."""
    text = str(text or "")
    out: list[str] = []
    for m in _ID_RUN.finditer(text):
        out.append(f"a long number that could be an ID ({m.group()})")
    for m in _DATE.finditer(text):
        out.append(f"a date ({m.group()})")
    for m in _ID_WORDS.finditer(text):
        out.append(f"the words “{m.group()}”")
    for m in _NAMED.finditer(text):
        out.append(f"what may be a person's name ({m.group().strip()})")
    seen: set[str] = set()
    return [r for r in out if not (r in seen or seen.add(r))]


# -- reading ---------------------------------------------------------------------------------

def get(qid: str) -> dict | None:
    if not valid_id(qid):
        return None
    rows = _q("SELECT * FROM questions WHERE id = ?", (qid,))
    return rows[0] if rows else None


def uses(ids: list[str]) -> dict[str, dict]:
    """How often each question was asked from the library: times, people, last time."""
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = _q(f"SELECT question, COUNT(*) AS n, COUNT(DISTINCT user) AS people, MAX(at) AS last "
              f"FROM question_uses WHERE question IN ({marks}) GROUP BY question", list(ids))
    return {r["question"]: {"uses": r["n"], "people": r["people"], "last_used": r["last"]} for r in rows}


def _with_uses(rows: list[dict]) -> list[dict]:
    counts = uses([r["id"] for r in rows])
    for r in rows:
        r.update(counts.get(r["id"]) or {"uses": 0, "people": 0, "last_used": None})
    return rows


def mine(user: str, dataset: str | None = None) -> list[dict]:
    """A person's own questions, the ones they ask most first."""
    sql = "SELECT * FROM questions WHERE kind = 'mine' AND owner = ?"
    args: list = [user]
    if dataset:
        sql += " AND dataset = ?"
        args.append(dataset)
    rows = _with_uses(_q(sql, args))
    offered = {r["origin"] for r in _q(
        "SELECT origin FROM questions WHERE kind = 'suggestion' AND owner = ? AND status IN ('open', 'added')",
        (user,))}
    for r in rows:
        r["suggested"] = r["id"] in offered
    rows.sort(key=lambda r: r["last_used"] or r["updated"] or "", reverse=True)
    rows.sort(key=lambda r: -(r["uses"] or 0))
    return rows


def team(dataset: str, retired: bool = False) -> list[dict]:
    """A dataset's team questions: pinned first, then the most asked."""
    sql = "SELECT * FROM questions WHERE kind = 'team' AND dataset = ?"
    if not retired:
        sql += " AND status = 'active'"
    rows = _with_uses(_q(sql, (dataset,)))
    return sorted(rows, key=lambda r: (r["status"] != "active", -(r["pinned"] or 0), -(r["uses"] or 0),
                                       (r["name"] or r["text"]).casefold()))


def suggestions(dataset: str | None = None, status: str = "open") -> list[dict]:
    sql = "SELECT * FROM questions WHERE kind = 'suggestion' AND status = ?"
    args: list = [status]
    if dataset:
        sql += " AND dataset = ?"
        args.append(dataset)
    return _q(sql + " ORDER BY created DESC", args)


def attention() -> dict[str, int]:
    """Per dataset, what waits for an admin: team questions whose check needs a look,
    and open suggestions."""
    marks = ",".join("?" for _ in NEEDS_A_LOOK)
    rows = _q("SELECT dataset, COUNT(*) AS n FROM questions WHERE "
              f"(kind = 'team' AND status = 'active' AND check_status IN ({marks})) "
              "OR (kind = 'suggestion' AND status = 'open') GROUP BY dataset", list(NEEDS_A_LOOK))
    return {r["dataset"]: r["n"] for r in rows}


def checking() -> bool:
    """Whether a check is waiting or running."""
    return bool(_q("SELECT 1 FROM questions WHERE check_state IN ('queued', 'running') LIMIT 1"))


def public(q: dict | None) -> dict | None:
    """A question as the Ask page gets it: never the expected answer or what a check
    found, only whether the last check went well."""
    if not q:
        return None
    out = {"id": q["id"], "kind": q["kind"], "name": q.get("name") or "", "text": q["text"],
           "dataset": q["dataset"]}
    if q["kind"] == "mine":
        out.update(uses=q.get("uses") or 0, last_used=q.get("last_used"), created=q.get("created"),
                   suggested=bool(q.get("suggested")))
    elif q["kind"] == "team":
        out["pinned"] = bool(q.get("pinned"))
        status = q.get("check_status")
        if status == "matched" and q.get("expected"):
            out["checked"] = q.get("check_at")
        elif status in ("changed", "failed"):
            out["review"] = True
        if q.get("expected") == "":
            out["no_number"] = True
    return out


def admin_view(q: dict | None) -> dict | None:
    """A question as the Admin page gets it: everything kept about it."""
    if not q:
        return None
    out = {k: q.get(k) for k in (
        "id", "kind", "owner", "dataset", "name", "text", "created", "updated", "status", "origin",
        "expected", "expected_level", "expected_at", "expected_by", "check_status", "check_value",
        "check_level", "check_at", "check_by", "check_trace", "check_thread", "check_note", "check_state",
        "handled_by", "handled_at", "uses", "people", "last_used")}
    out["pinned"] = bool(q.get("pinned"))
    return out


# -- writing ---------------------------------------------------------------------------------

def save_mine(user: str, dataset: str, text: str, name: str = "") -> dict:
    """Save a question of a person's own; the same question again keeps one copy."""
    text, name = clean_text(text), clean_name(name)
    if not text:
        raise ValueError("Write the question first.")
    for r in _q("SELECT * FROM questions WHERE kind = 'mine' AND owner = ? AND dataset = ?", (user, dataset)):
        if _norm(r["text"]) == _norm(text):
            if name and name != r.get("name"):
                _x("UPDATE questions SET name = ?, updated = ? WHERE id = ?", (name, now(), r["id"]))
            return get(r["id"])
    n = _q("SELECT COUNT(*) AS n FROM questions WHERE kind = 'mine' AND owner = ? AND dataset = ?",
           (user, dataset))[0]["n"]
    if n >= MAX_MINE:
        raise ValueError(f"You have saved {MAX_MINE} questions for this dataset. Delete some first.")
    qid, t = _new_id(), now()
    _x("INSERT INTO questions (id, kind, owner, dataset, name, text, created, updated) "
       "VALUES (?, 'mine', ?, ?, ?, ?, ?, ?)", (qid, user, dataset, name, text, t, t))
    return get(qid)


def add_team(actor: str, dataset: str, text: str, name: str = "", pinned: bool = False,
             expected: str | None = None, expected_level: str | None = None,
             trace: str | None = None, thread: str | None = None, origin: str | None = None) -> dict:
    """Make a team question. With `expected` (the main number of an answer the admin
    looked at, "" for an answer with no single number) it starts out checked by them."""
    text, name = clean_text(text), clean_name(name)
    if not text:
        raise ValueError("Write the question first.")
    for r in _q("SELECT * FROM questions WHERE kind = 'team' AND dataset = ? AND status = 'active'", (dataset,)):
        if _norm(r["text"]) == _norm(text):
            if pinned and not r.get("pinned"):
                _x("UPDATE questions SET pinned = 1, updated = ? WHERE id = ?", (now(), r["id"]))
            return get(r["id"])
    n = _q("SELECT COUNT(*) AS n FROM questions WHERE kind = 'team' AND dataset = ?", (dataset,))[0]["n"]
    if n >= MAX_TEAM:
        raise ValueError(f"This dataset has {MAX_TEAM} team questions. Delete retired ones first.")
    qid, t = _new_id(), now()
    _x("INSERT INTO questions (id, kind, owner, dataset, name, text, created, updated, pinned, origin) "
       "VALUES (?, 'team', ?, ?, ?, ?, ?, ?, ?, ?)", (qid, actor, dataset, name, text, t, t, 1 if pinned else 0, origin))
    if expected is not None:
        _x("UPDATE questions SET expected = ?, expected_level = ?, expected_at = ?, expected_by = ?, "
           "check_status = ?, check_value = ?, check_level = ?, check_at = ?, check_by = ?, "
           "check_trace = ?, check_thread = ? WHERE id = ?",
           (expected, expected_level, t, actor, "matched" if expected else "answered", expected,
            expected_level, t, actor, trace, thread, qid))
    return get(qid)


def suggest(user: str, qid: str) -> dict:
    """Offer one of a person's own questions to the admins (once)."""
    q = get(qid)
    if not q or q["kind"] != "mine" or q["owner"] != user:
        raise ValueError("No such question of yours.")
    have = _q("SELECT id FROM questions WHERE kind = 'suggestion' AND origin = ? AND status IN ('open', 'added')",
              (qid,))
    if have:
        return get(have[0]["id"])
    sid, t = _new_id(), now()
    _x("INSERT INTO questions (id, kind, owner, dataset, name, text, created, updated, status, origin) "
       "VALUES (?, 'suggestion', ?, ?, ?, ?, ?, ?, 'open', ?)",
       (sid, user, q["dataset"], q.get("name") or "", q["text"], t, t, qid))
    return get(sid)


def edit(qid: str, *, name=None, text=None, pinned=None) -> dict:
    """Change a question's name, words or pin. New words on a team question clear its
    checked mark: the expected answer was for the old words, and the next check says
    whether it still holds."""
    q = get(qid)
    if not q:
        raise ValueError("No such question.")
    sets, args = [], []
    if name is not None:
        sets.append("name = ?")
        args.append(clean_name(name))
    if text is not None:
        text = clean_text(text)
        if not text:
            raise ValueError("A question cannot be empty.")
        if text != q["text"]:
            sets.append("text = ?")
            args.append(text)
            if q["kind"] == "team" and q.get("check_status") in ("matched", "answered"):
                sets.append("check_status = NULL")
    if pinned is not None and q["kind"] == "team":
        sets.append("pinned = ?")
        args.append(1 if pinned else 0)
    if sets:
        sets.append("updated = ?")
        args.append(now())
        _x(f"UPDATE questions SET {', '.join(sets)} WHERE id = ?", args + [qid])
    return get(qid)


def delete(qid: str) -> bool:
    con = _connect()
    try:
        n = con.execute("DELETE FROM questions WHERE id = ?", (qid,)).rowcount
        con.execute("DELETE FROM question_uses WHERE question = ?", (qid,))
        return n > 0
    finally:
        con.close()


def set_status(qid: str, status: str, actor: str) -> dict:
    q = get(qid)
    if not q:
        raise ValueError("No such question.")
    allowed = {"team": ("active", "retired"), "suggestion": ("open", "added", "dismissed")}.get(q["kind"], ())
    if status not in allowed:
        raise ValueError(f"A {q['kind']} question cannot be {status!r}.")
    t = now()
    _x("UPDATE questions SET status = ?, updated = ?, handled_by = ?, handled_at = ? WHERE id = ?",
       (status, t, actor, t, qid))
    return get(qid)


def record_use(qid: str, user: str) -> None:
    _x("INSERT INTO question_uses (question, user, at) VALUES (?, ?, ?)", (qid, user, now()))


def delete_users(users: list[str]) -> int:
    """Remove people's own questions, suggestions and use counts (test accounts)."""
    if not users:
        return 0
    marks = ",".join("?" for _ in users)
    con = _connect()
    try:
        ids = [r[0] for r in con.execute(
            f"SELECT id FROM questions WHERE kind IN ('mine', 'suggestion') AND owner IN ({marks})", users)]
        n = con.execute(f"DELETE FROM questions WHERE kind IN ('mine', 'suggestion') AND owner IN ({marks})",
                        users).rowcount
        con.execute(f"DELETE FROM question_uses WHERE user IN ({marks})", users)
        if ids:
            con.execute(f"DELETE FROM question_uses WHERE question IN ({','.join('?' for _ in ids)})", ids)
        return n
    finally:
        con.close()


# -- checks ------------------------------------------------------------------------------------

def compare(expected: str | None, found: int | None, answered: bool) -> str:
    """What a check's answer says about a team question (see NEEDS_A_LOOK)."""
    if not answered:
        return "failed"
    if expected is None:
        return "new"
    if expected == "":
        return "answered"
    if found is None:
        return "changed"
    return "matched" if str(found) == str(expected) else "changed"


def check_queue(qid: str) -> bool:
    """Mark an active team question as waiting for a check, unless one already is."""
    return _x("UPDATE questions SET check_state = 'queued' WHERE id = ? AND kind = 'team' "
              "AND status = 'active' AND check_state IS NULL", (qid,)) > 0


def check_running(qid: str) -> None:
    _x("UPDATE questions SET check_state = 'running' WHERE id = ?", (qid,))


def check_result(qid: str, *, status: str, actor: str, value: str | None = None, level: str | None = None,
                 trace: str | None = None, thread: str | None = None, note: str | None = None) -> None:
    _x("UPDATE questions SET check_status = ?, check_value = ?, check_level = ?, check_at = ?, check_by = ?, "
       "check_trace = ?, check_thread = ?, check_note = ?, check_state = NULL WHERE id = ?",
       (status, value, level, now(), actor, trace, thread, (note or None) and str(note)[:500], qid))


def check_cancel(qid: str) -> None:
    """A queued check that will not run (the question was retired or deleted meanwhile)."""
    _x("UPDATE questions SET check_state = NULL WHERE id = ?", (qid,))


def clear_unfinished() -> int:
    """Checks a stopped server left waiting or running: none of them will finish."""
    try:
        return _x("UPDATE questions SET check_state = NULL, check_status = 'failed', check_at = ?, "
                  "check_note = 'The Ask server restarted before this check finished.' "
                  "WHERE check_state IN ('queued', 'running')", (now(),))
    except Exception as exc:  # noqa: BLE001
        print(f"question library: {exc}", file=sys.stderr)
        return 0


def keep(qid: str, actor: str) -> dict:
    """Take what the last check found as the expected answer from now on."""
    q = get(qid)
    if not q or q["kind"] != "team":
        raise ValueError("No such team question.")
    if q.get("check_status") not in ("changed", "new", "answered", "matched"):
        raise ValueError("There is no answer from a check to keep.")
    value = q.get("check_value") or ""
    t = now()
    _x("UPDATE questions SET expected = ?, expected_level = ?, expected_at = ?, expected_by = ?, "
       "check_status = ?, check_note = NULL, updated = ? WHERE id = ?",
       (value, q.get("check_level"), t, actor, "matched" if value else "answered", t, qid))
    return get(qid)


def is_check_thread(thread: str) -> bool:
    if not thread:
        return False
    return bool(_q("SELECT 1 FROM questions WHERE kind = 'team' AND check_thread = ? LIMIT 1", (thread,)))
