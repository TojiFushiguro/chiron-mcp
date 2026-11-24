"""The question library through the Ask server, as the Ask page and the Admin page use
it: who may save, see, change, suggest, publish and check what (chiron_mcp/library.py
and the /questions calls in chiron_mcp/webapp.py).

As five identities on one dataset (accounts made for the run, removed afterwards):

    lib_deid     de-identified          saves and suggests; sees the team questions
    lib_agg      aggregate only         the same
    lib_none     a login with no row    no team questions; cannot save on the dataset
    lib_admin    a superuser            adds, pins, retires, checks team questions
    lib_admin2   another superuser      reads a check's answer that lib_admin ran

Without a model (the default) nothing is asked. With --model, a team question is asked
from the library (its use is counted; another person's saved question is not) and
checked: the answer's main number is compared, the check's conversation is kept out of
the admin's own list, other admins may read it and nobody else may, and the usage
ledger marks it as a check.

    ASK_URL=http://localhost:8900 TEST_DATASET=synthea-small \\
    .venv/bin/python tests/library_http.py            # --model, --keep

Needs the server's CHIRON_MCP_* database settings in the environment (sessions and the
accounts are made in-process), like tests/permissions.py; with the server's
CHIRON_MCP_QUESTIONS_DB (or CHIRON_MCP_STATE_DIR) too, the run also removes the test
accounts' suggestions from the library file.

Exit code 0 means every check held.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ask_e2e as E  # noqa: E402  (sessions, ORIGIN, the conversations a run starts)

ASK, DS = E.ASK, E.DS
ONLY_ADMINS = "Only a Chiron superuser can manage team questions."
NO_ACCESS = "You have no access to this dataset."
# All the Ask page may ever get about a question: its words, and whether it is checked.
PUBLIC_KEYS = {"id", "kind", "name", "text", "dataset", "pinned", "checked", "review", "no_number",
               "uses", "last_used", "created", "suggested"}
IDENTITIES = {
    "lib_deid": ("deid", False),
    "lib_agg": ("agg", False),
    "lib_none": None,
    "lib_admin": "superuser",
    "lib_admin2": "superuser",
}
TEAM_Q = "How many patients have type 2 diabetes?"

fails: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {json.dumps(detail, default=str)[:240] if detail != '' else ''}")
    if not cond:
        fails.append(label)


# -- accounts ------------------------------------------------------------------------------

def make_accounts() -> None:
    from chiron_mcp.bootstrap import ensure_django

    ensure_django()
    from django.contrib.auth import get_user_model

    from chiron import models

    User = get_user_model()
    ds = models.Dataset.objects.filter(unique_id=DS).first()
    if ds is None:
        sys.exit(f"no dataset {DS!r} (TEST_DATASET)")
    for name, spec in IDENTITIES.items():
        user, _made = User.objects.get_or_create(username=name, defaults={"is_active": True})
        user.set_unusable_password()
        user.is_active = True
        user.is_staff = user.is_superuser = spec == "superuser"
        user.save()
        models.ChironUser.objects.filter(user=user).delete()   # exactly the rows below
        if spec is None:
            continue
        level, details = ("deid", False) if spec == "superuser" else spec
        cu = models.ChironUser(user=user, dataset=ds, access_level=level,
                               can_view_workspace=True, can_view_subject_details=details)
        cu.save()
        for default in models.DefaultPermissionGroup.objects.filter(dataset=ds):
            cu.permission_groups.add(default.permission_group)


def remove_accounts() -> None:
    from django.contrib.auth import get_user_model

    from chiron_mcp import library, usage

    names = list(IDENTITIES)
    left = library.delete_users(names) if (library.setting() or os.environ.get("CHIRON_MCP_STATE_DIR")) else 0
    rows = usage.delete_users(names)
    get_user_model().objects.filter(username__in=names).delete()
    print(f"removed {len(names)} accounts, {left} library row(s) and {rows} ledger row(s) of theirs")


# -- requests --------------------------------------------------------------------------------

SESSIONS: dict[str, str] = {}


def req(method: str, path: str, who: str | None = None, body: dict | None = None,
        headers: dict | None = None) -> tuple[int, dict]:
    h = dict(headers or {})
    if who:
        h["Cookie"] = f"sessionid={SESSIONS[who]}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
        h.setdefault("Origin", E.ORIGIN)
    r = urllib.request.Request(ASK + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def lib(who: str) -> dict:
    return req("GET", "/questions?dataset=" + urllib.parse.quote(DS), who)[1]


def post(who: str, action: str, body: dict) -> dict:
    return req("POST", "/questions/" + action, who, body)[1]


def admin_view(who: str = "lib_admin") -> dict:
    return req("GET", "/questions/admin.json?dataset=" + urllib.parse.quote(DS), who)[1]


def mine(who: str) -> list[dict]:
    return lib(who).get("mine") or []


def team(who: str) -> list[dict]:
    return lib(who).get("team") or []


def ask(who: str, q: str, picked: str | None = None) -> dict:
    """One question through /ask as the page sends it, from the library when `picked`."""
    params = {"q": q, "dataset": DS}
    if picked:
        params["from"] = picked
    r = urllib.request.Request(f"{ASK}/ask?" + urllib.parse.urlencode(params),
                               headers={"Cookie": f"sessionid={SESSIONS[who]}"})
    events: dict = {}
    event = None
    with urllib.request.urlopen(r, timeout=360) as resp:
        for raw in resp:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                events[event] = json.loads(line[6:])
                if event == "done":
                    break
    if isinstance(events.get("thread"), str):
        E.STARTED.add((events["thread"], SESSIONS[who]))
    return events


# -- the checks ---------------------------------------------------------------------------------

def run(model: bool) -> list[str]:
    made: list[str] = []      # team questions this run added, deleted at the end

    # who sees what
    for who in IDENTITIES:
        d = lib(who)
        want = {"lib_deid": "deid", "lib_agg": "agg", "lib_none": None}.get(who, "any")
        check(f"{who}: the library is on, at their level", d.get("on") and not d.get("error")
              and (want == "any" or d.get("level") == want), d)
        check(f"{who}: admin only as a superuser", bool(d.get("admin")) == (who.startswith("lib_admin")), d.get("admin"))

    # saving
    r = post("lib_deid", "save", {"dataset": DS, "text": "How many patients have asthma?", "name": "Asthma"})
    mine_id = r.get("saved")
    check("a person saves a question, and it is in their list", r.get("ok") and mine_id
          and any(q["id"] == mine_id for q in r.get("mine") or []), r)
    check("nobody else's list has it", all(mine_id not in {q["id"] for q in mine(w)} for w in ("lib_agg", "lib_admin")))
    check("the page gets words only", all(set(q) <= PUBLIC_KEYS for q in mine("lib_deid")), mine("lib_deid"))
    r = post("lib_none", "save", {"dataset": DS, "text": "How many patients are there?"})
    check("no access to the dataset: nothing saved on it", r.get("error") == NO_ACCESS, r)
    r = post("lib_deid", "save", {"dataset": DS, "text": "A question for the whole team", "team": True})
    check("a non-admin cannot add a team question by saving, and nothing is saved",
          r.get("error") == ONLY_ADMINS and not any("whole team" in q["text"] for q in mine("lib_deid")), r)

    # another person's question
    for action in ("edit", "delete", "suggest"):
        r = post("lib_agg", action, {"id": mine_id, "name": "mine now", "text": "changed"})
        check(f"{action} on another person's question: there is no such question", r.get("error") == "No such question.", r)
    r = post("lib_admin", "delete", {"id": mine_id})
    check("not even an admin deletes a person's own question", r.get("error") == "No such question.", r)
    kept = next((q for q in mine("lib_deid") if q["id"] == mine_id), {})
    check("and it is as it was", kept.get("name") == "Asthma" and kept.get("text") == "How many patients have asthma?", kept)

    # what only admins do
    for action, body in (("team", {"dataset": DS, "text": "How many patients are there?"}),
                         ("status", {"id": mine_id, "status": "retired"}),
                         ("check", {"dataset": DS, "all": True}),
                         ("keep", {"id": mine_id})):
        r = post("lib_deid", action, body)
        check(f"a non-admin cannot {action}", r.get("error") == ONLY_ADMINS, r)
    r = admin_view("lib_deid")
    check("a non-admin cannot read the Team questions tab", r.get("error") == ONLY_ADMINS and "team" not in r, r)
    r = req("GET", "/questions/expected?trace=" + "0" * 32, "lib_agg")[1]
    check("a non-admin cannot read an expected answer", r.get("error") == ONLY_ADMINS, r)

    # team questions
    r = post("lib_admin", "team", {"dataset": DS, "text": TEAM_Q, "name": "Type 2 diabetes", "pinned": True})
    team_id = r.get("team_id")
    made += [team_id] if team_id else []
    check("an admin adds a pinned team question", r.get("ok") and team_id, r)
    for who in ("lib_deid", "lib_agg", "lib_admin2"):
        item = next((q for q in team(who) if q["id"] == team_id), None)
        check(f"{who} sees it, pinned", item and item.get("pinned"), item)
        check(f"{who} gets its words only, never an expected answer", item and set(item) <= PUBLIC_KEYS, item)
    check("someone with no access to the dataset gets no team questions", not team("lib_none"))
    r = post("lib_deid", "edit", {"id": team_id, "pinned": False})
    check("a non-admin cannot change a team question", r.get("error") == ONLY_ADMINS, r)
    r = post("lib_agg", "delete", {"id": team_id})
    check("a non-admin cannot delete a team question", r.get("error") == ONLY_ADMINS, r)
    r = post("lib_admin", "edit", {"id": team_id, "pinned": False, "view": "admin"})
    row = next((q for q in r.get("team") or [] if q["id"] == team_id), {})
    check("an admin unpins it, and gets the Admin page's view back", r.get("ok") and row and not row.get("pinned"), row)
    post("lib_admin", "edit", {"id": team_id, "pinned": True})

    # words that may point at one person
    risky = "Show the visits of MRN 12345678"
    r = post("lib_admin", "team", {"dataset": DS, "text": risky})
    check("words that may point at one person need a second press", r.get("ok") is False and r.get("confirm"), r)
    check("and nothing was added before it", not any(risky in q["text"] for q in team("lib_admin")))
    r = post("lib_admin", "team", {"dataset": DS, "text": risky, "confirm": True})
    made += [r["team_id"]] if r.get("team_id") else []
    check("added once confirmed", r.get("ok") and r.get("team_id"), r)

    # suggestions
    r = post("lib_deid", "suggest", {"id": mine_id})
    check("a person suggests their own question", r.get("ok"), r)
    post("lib_deid", "suggest", {"id": mine_id})
    a = admin_view()
    sugg = [s for s in a.get("suggestions") or [] if s.get("origin") == mine_id]
    check("the admin sees it once, and who suggested it", len(sugg) == 1 and sugg[0].get("owner") == "lib_deid"
          and sugg[0].get("owner_name"), a.get("suggestions"))
    check("the tab counts what waits", (a.get("attention") or 0) >= 1, a.get("attention"))
    r = post("lib_admin", "team", {"from": sugg[0]["id"], "view": "admin"}) if sugg else {}
    added = r.get("team_id")
    made += [added] if added else []
    check("the admin adds it to Team questions", r.get("ok") and added, r)
    check("the suggestion no longer waits", not any(s.get("origin") == mine_id for s in r.get("suggestions") or []))
    check("the person's saved question says it was suggested",
          next((q for q in mine("lib_deid") if q["id"] == mine_id), {}).get("suggested"))
    r = post("lib_agg", "save", {"dataset": DS, "text": "How many patients are older than 65?"})
    other = r.get("saved")
    post("lib_agg", "suggest", {"id": other})
    sid = next((s["id"] for s in admin_view().get("suggestions") or [] if s.get("origin") == other), None)
    r = post("lib_admin", "status", {"id": sid, "status": "dismissed", "view": "admin"})
    check("an admin dismisses a suggestion", r.get("ok") and not any(s["id"] == sid for s in r.get("suggestions") or []), r)

    # retire and restore
    post("lib_admin", "status", {"id": added, "status": "retired"})
    check("a retired question leaves people's list", added not in {q["id"] for q in team("lib_deid")})
    check("and stays on the Admin page", added in {q["id"] for q in admin_view().get("team") or []})
    post("lib_admin", "status", {"id": added, "status": "active"})
    check("restored, it is back", added in {q["id"] for q in team("lib_deid")})

    # the Admin page gets what the Ask page never does
    a = admin_view()
    row = next((q for q in a.get("team") or [] if q["id"] == team_id), {})
    check("the Admin page gets what is kept about a team question",
          {"expected", "check_status", "owner", "uses", "people"} <= set(row) and row.get("owner") == "lib_admin", row)
    check("and the datasets to pick from", any(d.get("id") == DS for d in a.get("datasets") or []), a.get("datasets"))

    # the web server's own rules
    st, r = req("POST", "/questions/save", "lib_deid", {"dataset": DS, "text": "evil"}, {"Origin": "https://evil.example"})
    check("a cross-site write is refused", str(r.get("error", "")).startswith("Refused"), r)
    st, r = req("GET", "/questions?dataset=" + DS, "lib_deid", headers={"Sec-Fetch-Site": "cross-site"})
    check("a cross-site read is refused", st == 403, st)
    st, r = req("GET", "/questions/admin.json?dataset=" + DS, "lib_admin", headers={"Sec-Fetch-Site": "cross-site"})
    check("a cross-site read of the Admin tab is refused", st == 403, st)
    st, r = req("GET", "/questions?dataset=" + DS)
    check("no session: nobody, and no questions", r.get("error") and not r.get("mine") and not r.get("team"), r)
    st, r = req("POST", "/questions/save", None, {"dataset": DS, "text": "anonymous"})
    check("no session: nothing saved", r.get("error"), r)
    check("an unknown action is refused", post("lib_deid", "nonsense", {}).get("error") == "No such action.")
    check("a made-up id is no such question", post("lib_deid", "delete", {"id": "zz"}).get("error") == "No such question.")

    if not model:
        print("(the model checks were skipped; --model asks and checks a team question)")
        return made

    # asking from the library
    ev = ask("lib_deid", TEAM_Q, picked=team_id)
    check("a team question asked from the library is answered", ev.get("answer"), ev.get("error"))
    ask("lib_agg", "How many patients have asthma?", picked=mine_id)   # another person's saved question
    row = next((q for q in admin_view().get("team") or [] if q["id"] == team_id), {})
    check("its use is counted: once, one person", row.get("uses") == 1 and row.get("people") == 1, row)
    check("asking another person's saved question counts nothing",
          next((q for q in mine("lib_deid") if q["id"] == mine_id), {}).get("uses") == 0)

    # a check
    r = post("lib_admin", "check", {"id": team_id, "view": "admin"})
    check("an admin starts a check", r.get("queued") == 1, r)
    deadline, row = time.time() + 420, {}
    while time.time() < deadline:
        row = next((q for q in admin_view().get("team") or [] if q["id"] == team_id), {})
        if not row.get("check_state"):
            break
        time.sleep(5)
    check("the check finishes", not row.get("check_state") and row.get("check_status"), row)
    check("its first answer waits for the admin to keep it", row.get("check_status") == "new"
          and row.get("check_value") and row.get("check_thread"), row)
    thread = row.get("check_thread")
    listed = {t["thread"] for t in req("GET", "/threads?dataset=" + DS, "lib_admin")[1].get("threads") or []}
    check("the check's conversation is not in the admin's own list", thread and thread not in listed)
    v = req("GET", "/threads?thread=" + str(thread), "lib_admin")[1]
    check("the admin who ran it can open its answer", v.get("turns") and not v.get("error"), v.get("error"))
    v = req("GET", "/threads?thread=" + str(thread), "lib_admin2")[1]
    check("another admin can open its answer too, read-only", v.get("turns") and v.get("read_only"), v.get("error"))
    v = req("GET", "/threads?thread=" + str(thread), "lib_deid")[1]
    check("nobody else can open it", v.get("error") and not v.get("turns"), v)
    r = post("lib_admin", "keep", {"id": team_id, "view": "admin"})
    row = next((q for q in r.get("team") or [] if q["id"] == team_id), {})
    check("keep: its number is the expected answer now", row.get("expected") == row.get("check_value")
          and row.get("check_status") == "matched", row)
    item = next((q for q in team("lib_deid") if q["id"] == team_id), {})
    check("people see it checked, still without the number", item.get("checked") and set(item) <= PUBLIC_KEYS, item)
    u = req("GET", "/usage.json?period=today&recent=200", "lib_admin")[1]
    check("the usage ledger marks the check as one",
          any(x.get("source") == "check" and x.get("user") == "lib_admin" for x in u.get("recent") or []))
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", action="store_true", help="also ask and check a team question (uses the model)")
    ap.add_argument("--keep", action="store_true", help="keep the lib_* accounts and their questions afterwards")
    args = ap.parse_args()
    make_accounts()
    for name in IDENTITIES:
        SESSIONS[name] = E.mint_session(name)
    made: list[str] = []
    try:
        made = run(args.model)
    finally:
        if not args.keep:
            for qid in made:
                post("lib_admin", "delete", {"id": qid})
            for who in ("lib_deid", "lib_agg"):
                for q in mine(who):
                    post(who, "delete", {"id": q["id"]})
            for tid, cookie in E.STARTED:
                try:
                    E.post_json(f"{ASK}/forget", {"thread": tid}, cookie)
                except Exception:  # noqa: BLE001
                    pass
            remove_accounts()
    print()
    print(f"{len(fails)} failed" if fails else "all checks held")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
