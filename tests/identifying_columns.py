"""An identity that may not view subject details gets no column that identifies a person.

Offline: no database, no model; the rule and the gate are checked on stand-in concepts.

    .venv/bin/python tests/identifying_columns.py
"""

import os
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
tmp = tempfile.mkdtemp()
os.environ.update(CHIRON_MCP_USERNAME="tester", CHIRON_MCP_STATE_DIR=os.path.join(tmp, "state"), CHIRON_MCP_TRACE="0")

from chiron_mcp import server as S  # noqa: E402
from chiron_mcp.identity import AccessError  # noqa: E402

fails: list[str] = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}"[:200])
    if not ok:
        fails.append(name)


def concept(short, has_phi=False, name=None):
    return NS(permanent_id=f"ds__subject__{short}", has_phi=has_phi, name=name or short)


first, last, ssn, city, birth, pid = (concept(x) for x in ("first", "last", "ssn", "city", "birthdate", "id"))
flagged = concept("mrn", has_phi=True, name="medical record number")

check("names, ssn and a has_phi concept identify a person",
      all(S._identifies_person(c) for c in (first, last, ssn, flagged)))
check("city, birthdate and the patient id do not", not any(S._identifies_person(c) for c in (city, birth, pid)))

deid = NS(can_view_subject_details=False)
full = NS(can_view_subject_details=True)
try:
    S._require_columns_viewable(deid, [pid, first, last, city])
    check("a de-identified identity is refused the name columns", False, "no error")
except AccessError as exc:
    check("a de-identified identity is refused the name columns", "first, last" in str(exc) and "switched off" in str(exc), str(exc)[:120])
try:
    S._require_columns_viewable(deid, [pid, city, birth])
    check("the same identity may list id, city and birthdate", True)
except AccessError as exc:
    check("the same identity may list id, city and birthdate", False, str(exc)[:120])
try:
    S._require_columns_viewable(full, [pid, first, last, ssn])
    check("an identity that may view subject details is not refused", True)
except AccessError as exc:
    check("an identity that may view subject details is not refused", False, str(exc)[:120])

# The refusal names the real reason: the names switch, not the level. A full-access row
# without the switch was told "this account (de-identified access)" until 2026-11-17, and
# its owner, an admin, was sent to "whoever manages your Chiron account".
def row(level, raw, admin):
    return NS(access_level=level, can_view_subject_details=False,
              chironuser=NS(access_level=raw, user=NS(is_superuser=admin)))


def refusal(ident):
    try:
        S._require_columns_viewable(ident, [first, last, city])
    except AccessError as exc:
        return str(exc)
    return ""


capped = refusal(row("deid", "phi", True))
check("a full-access admin without the switch is told the switch, not 'de-identified access'",
      "switched off" in capped and "full access (Ask uses at most de-identified)" in capped
      and "de-identified access" not in capped, capped[:160])
check("... and that they can switch it on themselves on the Admin page",
      "switch them on for itself" in capped and "Admin page" in capped, capped[:160])
plain = refusal(row("deid", "deid", False))
check("a de-identified account is told its level and that an admin can switch it on",
      "which is de-identified." in plain and "An admin can switch them on" in plain, plain[:160])
concept_msg = ""
try:
    S._require_concept_viewable(row("phi", "phi", False), first, "used as a filter")
except AccessError as exc:
    concept_msg = str(exc)
check("a filter on a name says the same", "switched off" in concept_msg and "which is full access." in concept_msg
      and "used as a filter" in concept_msg, concept_msg[:160])

print()
print("all passed" if not fails else f"{len(fails)} failed: {fails}")
sys.exit(1 if fails else 0)
