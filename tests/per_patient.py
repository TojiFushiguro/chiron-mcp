"""Figures per patient, time between events, counts over time, and 'either' groups
(chiron_query's show="per_patient", "time_between", "over_time", records' "age", and
match="any" across different variables with more than a count).

In-process, no model: each figure the tool hands the model is compared with a direct SQL
read of the warehouse. The first check is the question that ran out of time on
2026-11-10 ("which patient has the largest number of systolic bp values in their
record?"), which took 14 cohort queries and four minutes; it must now be one call of a
few seconds, with the same patients and counts as SQL.

Needs the server's CHIRON_MCP_* database settings in the environment (as tests/ask_e2e.py).
Runs as demouser, and makes (then removes) the permission suite's perm_* accounts for the
access checks. TEST_DATASET names the dataset (synthea-10k); TEST_PATIENT_BIRTHDATE a
patient of it with many records (default 1929-12-14).

    .venv/bin/python tests/per_patient.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CHIRON_MCP_USERNAME", "demouser")

import ask_e2e as E  # noqa: E402  (the warehouse connection and schema names)
import permissions as PM  # noqa: E402  (the perm_* accounts)

DS = E.DS
P = E.P
BORN = os.environ.get("TEST_PATIENT_BIRTHDATE", "1929-12-14")
fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {str(detail)[:260]}", flush=True)
    if not cond:
        fails.append(label)


def sql(q: str) -> list[tuple]:
    import psycopg2

    with psycopg2.connect(E.WAREHOUSE) as conn, conn.cursor() as cur:
        cur.execute(f"set search_path to {E.SCHEMA}")
        cur.execute(q)
        return [tuple(v.isoformat() if hasattr(v, "isoformat") else v for v in r) for r in cur.fetchall()]


def lk(coll: str, short: str, alias: str, on: str) -> str:
    return f'join "lkp_{P}{coll}__{short}" {alias} on {alias}._collection_id = {on}._id'


def c(alias: str, coll: str, short: str) -> str:
    return f'{alias}."{P}{coll}__{short}"'


def run(where, show, user: str = "demouser", match: str = "all") -> tuple[dict, float]:
    from chiron_mcp import query
    from chiron_mcp.config import CONFIG

    before = CONFIG.username
    CONFIG.username = user
    t0 = time.time()
    try:
        try:
            r = query.run(DS, where, match, None, show, None)
        except Exception as exc:  # noqa: BLE001
            r = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        CONFIG.username = before
    return r, time.time() - t0


def num(v, places: int = 2):
    return None if v is None else round(float(v), places)


# Patients of the asthma cohort as the e2e truths define it, and the patient by birthdate.
ASTHMA = f"select c._subject_id from condition c {lk('condition', 'description', 'l', 'c')} where lower({c('l', 'condition', 'description')}) in ('asthma', 'asthm')"
HER = f"select s._subject_id from subject s {lk('subject', 'birthdate', 'b', 's')} where {c('b', 'subject', 'birthdate')} = '{BORN}'"
PID = f"{lk('subject', 'id', 'i', 's')}"


def main() -> int:
    from chiron_mcp.bootstrap import ensure_django

    ensure_django()
    tag = "[per patient]"
    who = [{"field": "birthdate", "is": BORN}]
    asthma = [{"condition": "asthma"}]

    # --- the question that ran out of time: the most systolic readings, whole dataset
    truth = sql(f"select {c('i', 'subject', 'id')}, count(distinct o._id) from subject s {PID} "
                f"join observation o on o._subject_id = s._subject_id {lk('observation', 'description', 'd', 'o')} "
                f"where lower({c('d', 'observation', 'description')}) like 'systolic%' group by 1 order by 2 desc, 1")
    by_id = dict(truth)
    r, secs = run([], {"per_patient": {"of": {"lab": "Systolic Blood Pressure"}, "n": 5}})
    pp = r.get("per_patient") or {}
    rows = pp.get("rows") or []
    check(f"{tag} Michael's question is one call of a few seconds, not four minutes", not r.get("error") and secs < 20,
          f"{secs:.1f}s error={r.get('error')}")
    check(f"{tag} the five patients with the most readings, each with SQL's count for them",
          len(rows) == 5 and all(by_id.get(pid) == n for pid, n in rows) and [n for _, n in rows] == [n for _, n in truth[:5]],
          f"tool={rows} sql={truth[:5]}")
    fifth = truth[4][1]
    tied = sum(1 for _, n in truth if n == fifth) - sum(1 for _, n in rows if n == fifth)
    check(f"{tag} ties at the last row are counted and said", pp.get("tied_at_cutoff") == tied
          and (not tied or any("shares the last row" in n or "share the last row" in n for n in r.get("notes") or [])),
          f"tool={pp.get('tied_at_cutoff')} sql={tied} notes={r.get('notes')}")
    counts = [n for _, n in truth]
    import statistics

    check(f"{tag} patients, records and the spread across patients match SQL",
          pp.get("patients") == len(truth) and pp.get("records") == sum(counts)
          and (pp.get("across_patients") or {}).get("highest") == max(counts)
          and (pp.get("across_patients") or {}).get("lowest") == min(counts)
          and num((pp.get("across_patients") or {}).get("median")) == num(statistics.median(counts))
          and num((pp.get("across_patients") or {}).get("mean")) == num(sum(counts) / len(counts)),
          f"tool={ {k: pp.get(k) for k in ('patients', 'records', 'across_patients')} } sql=patients {len(truth)} records {sum(counts)}")
    check(f"{tag} the answer guidance leads with the ranking, not the group's count",
          "per_patient" in (r.get("how_to_answer") or "") and "not as the lead" in (r.get("how_to_answer") or ""), r.get("how_to_answer"))
    r1, _ = run([], {"per_patient": {"of": {"lab": "Systolic Blood Pressure"}, "n": 1}})
    top = ((r1.get("per_patient") or {}).get("rows") or [[None, None]])[0]
    check(f"{tag} n=1 names the one patient with the most", top == [truth[0][0], truth[0][1]] if truth[0][1] > truth[1][1] else top[1] == truth[0][1],
          f"tool={top} sql={truth[:2]}")

    # --- fewest, totals, averages, per year
    truth = sql(f"select {c('i', 'subject', 'id')}, count(distinct e._id) from subject s {PID} "
                f"join encounter e on e._subject_id = s._subject_id where s._subject_id in ({ASTHMA}) group by 1 order by 2, 1")
    r, _ = run(asthma, {"per_patient": {"of": "visit", "order": "asc", "n": 3}})
    rows = (r.get("per_patient") or {}).get("rows") or []
    by_id = dict(truth)
    check(f"{tag} the fewest visits among asthma patients (order asc)",
          len(rows) == 3 and [n for _, n in rows] == [n for _, n in truth[:3]] and all(by_id.get(p) == n for p, n in rows),
          f"tool={rows} sql={truth[:3]}")
    truth = sql(f"select {c('i', 'subject', 'id')}, sum({c('k', 'encounter', 'total_claim_cost')}) from subject s {PID} "
                f"join encounter e on e._subject_id = s._subject_id {lk('encounter', 'total_claim_cost', 'k', 'e')} "
                f"where s._subject_id in ({ASTHMA}) group by 1 order by 2 desc, 1")
    r, _ = run(asthma, {"per_patient": {"of": "visit", "stat": "sum", "field": "total claim cost", "n": 3}})
    pp = r.get("per_patient") or {}
    rows = pp.get("rows") or []
    by_id = {p: num(v) for p, v in truth}
    check(f"{tag} total visit cost per patient (sum of a number field), the top three",
          len(rows) == 3 and all(by_id.get(p) == num(v) for p, v in rows) and [num(v) for _, v in rows] == [num(v) for _, v in truth[:3]],
          f"tool={rows} sql={truth[:3]}")
    check(f"{tag} its column says what it is", pp.get("columns") == ["patient", "total cost"] and pp.get("tied_at_cutoff") == 0,
          f"{pp.get('columns')} tied={pp.get('tied_at_cutoff')}")
    truth = sql(f"select avg({c('v', 'observation', 'value')}) from observation o {lk('observation', 'description', 'd', 'o')} "
                f"{lk('observation', 'value', 'v', 'o')} where o._subject_id in ({HER}) and lower({c('d', 'observation', 'description')}) like 'systolic%'")
    r, _ = run(who, {"per_patient": {"of": {"lab": "Systolic Blood Pressure"}, "stat": "average"}})
    rows = (r.get("per_patient") or {}).get("rows") or []
    check(f"{tag} her average systolic reading ('field' defaults to the lab's value)",
          len(rows) == 1 and num(rows[0][1], 2) == num(truth[0][0], 2), f"tool={rows} sql={truth}")
    truth = sql(f"select extract(year from {c('t', 'encounter', 'start')})::int, count(distinct e._id) from encounter e "
                f"{lk('encounter', 'start', 't', 'e')} where e._subject_id in ({HER}) group by 1 order by 1")
    r, _ = run(who, {"per_patient": {"of": "visit", "by": "year"}})
    rows = (r.get("per_patient") or {}).get("rows") or []
    check(f"{tag} her visits per year (by year), every year as SQL", [(y, n) for _, y, n in rows] == truth,
          f"tool={len(rows)} rows, first {rows[:2]} sql={len(truth)} rows, first {truth[:2]}")

    # --- refusals and caps
    for where, spec, why, words in (
            (asthma, {"of": "visit", "by": "year"}, "per year over a large group", "small group"),
            (who, {"of": "visit", "stat": "sum", "field": "visit type"}, "a sum of a field that is not a number", "not a number"),
            (who, {"of": "visit", "stat": "median"}, "a stat it does not compute", "'stat'"),
            (who, {"of": "visit", "bogus": 1}, "an unknown key", "does not take"),
            (who, {"of": "subject"}, "the patient is not a kind of record", "not a kind of record"),
            (who, {"of": {"visit": "emergency", "at_least": 2}}, "a count rule belongs in where", "count rule")):
        r, _ = run(where, {"per_patient": spec})
        check(f"{tag} refused: {why}", "error" in r and words in r["error"] and not (r.get("per_patient") or {}).get("rows"),
              r.get("error", "")[:140])
    r, _ = run(asthma, {"per_patient": {"of": "visit", "n": 5000}})
    check(f"{tag} n is capped at 100", len((r.get("per_patient") or {}).get("rows") or []) == 100, len((r.get("per_patient") or {}).get("rows") or []))

    # --- time between two events
    tag = "[time between]"
    truth = sql(f"with b as (select s._subject_id sid, {c('b', 'subject', 'birthdate')} d from subject s {lk('subject', 'birthdate', 'b', 's')} "
                f"  where s._subject_id in ({ASTHMA})), "
                f"t as (select c._subject_id sid, min({c('t', 'condition', 'start')}) d from condition c "
                f"  {lk('condition', 'description', 'l', 'c')} {lk('condition', 'start', 't', 'c')} join b on b.sid = c._subject_id "
                f"  where lower({c('l', 'condition', 'description')}) in ('asthma', 'asthm') and {c('t', 'condition', 'start')} >= b.d group by 1) "
                f"select count(*), min(a), percentile_cont(0.5) within group (order by a), avg(a), max(a) from "
                f"(select date_part('year', age(t.d, b.d))::int a from b join t using (sid)) x")
    r, _ = run(asthma, {"time_between": {"from": "birth", "to": {"condition": "asthma"}, "unit": "years"}})
    tb = r.get("time_between") or {}
    ap = tb.get("across_patients") or {}
    n, lo, med, mean, hi = truth[0]
    check(f"{tag} age at the first asthma diagnosis (from birth, in years), across the cohort as SQL",
          not r.get("error") and tb.get("patients_with_both") == n and ap.get("shortest") == lo and ap.get("longest") == hi
          and num(ap.get("median")) == num(med) and num(ap.get("mean")) == num(mean),
          f"error={r.get('error')} tool={tb.get('patients_with_both')} {ap} sql={truth[0]}")
    check(f"{tag} said in plain words", tb.get("from") == "birth" and tb.get("to") == "first condition: description is Asthma"
          and tb.get("columns") == ["patient", "from", "to", "years"], (tb.get("from"), tb.get("to"), tb.get("columns")))
    truth = sql(f"with a as (select c._subject_id sid, min({c('t', 'condition', 'start')}) d from condition c "
                f"  {lk('condition', 'description', 'l', 'c')} {lk('condition', 'start', 't', 'c')} "
                f"  where lower({c('l', 'condition', 'description')}) in ('asthma', 'asthm') group by 1), "
                f"m as (select m._subject_id sid, min({c('t', 'medication', 'start')}) d from medication m "
                f"  {lk('medication', 'description', 'l', 'm')} {lk('medication', 'start', 't', 'm')} join a on a.sid = m._subject_id "
                f"  where lower({c('l', 'medication', 'description')}) like 'albuterol%' and {c('t', 'medication', 'start')} >= a.d group by 1) "
                f"select count(*), min(m.d - a.d), percentile_cont(0.5) within group (order by m.d - a.d), avg(m.d - a.d), max(m.d - a.d), "
                f"(select count(*) from a) from a join m using (sid)")
    r, _ = run(asthma, {"time_between": {"from": {"condition": "asthma"}, "to": {"medication": "albuterol"}}})
    tb = r.get("time_between") or {}
    ap = tb.get("across_patients") or {}
    n, lo, med, mean, hi, n_from = truth[0]
    check(f"{tag} days from the first asthma diagnosis to the first albuterol on or after it, as SQL",
          not r.get("error") and tb.get("patients_with_both") == n and tb.get("patients_with_from") == n_from
          and ap.get("shortest") == lo and ap.get("longest") == hi and num(ap.get("median")) == num(med) and num(ap.get("mean")) == num(mean),
          f"error={r.get('error')} tool={tb.get('patients_with_from')}/{tb.get('patients_with_both')} {ap} sql={truth[0]}")
    rows = tb.get("rows") or []
    check(f"{tag} rows are per patient, shortest first, each from <= to", len(rows) == 10
          and all(row[1] <= row[2] for row in rows) and [row[3] for row in rows] == sorted(row[3] for row in rows), rows[:3])
    r2, _ = run(asthma, {"time_between": {"from": {"condition": "asthma"}, "to": {"medication": "lisinopril"}}})
    check(f"{tag} the second event's name filter really filters (albuterol and lisinopril differ)",
          (r2.get("time_between") or {}).get("patients_with_both") not in (None, n), (r2.get("time_between") or {}).get("patients_with_both"))
    truth = sql(f"select min({c('t', 'encounter', 'start')}), max({c('t', 'encounter', 'start')}), "
                f"date_part('year', age(max({c('t', 'encounter', 'start')}), min({c('t', 'encounter', 'start')})))::int "
                f"from encounter e {lk('encounter', 'start', 't', 'e')} where e._subject_id in ({HER})")
    r, _ = run(who, {"time_between": {"from": {"visit": "any", "which": "first"}, "to": {"visit": "any", "which": "last"}, "unit": "years"}})
    rows = (r.get("time_between") or {}).get("rows") or []
    check(f"{tag} her first visit to her last ('which': 'last'), as SQL", len(rows) == 1 and tuple(rows[0][1:]) == truth[0],
          f"tool={rows} sql={truth}")
    copd = f"select c._subject_id from condition c {lk('condition', 'description', 'l', 'c')} where lower({c('l', 'condition', 'description')}) like 'chronic obstructive%'"
    truth = sql(f"with a as (select c._subject_id sid, min({c('t', 'condition', 'start')}) d from condition c "
                f"  {lk('condition', 'description', 'l', 'c')} {lk('condition', 'start', 't', 'c')} "
                f"  where lower({c('l', 'condition', 'description')}) like 'chronic obstructive%' group by 1), "
                f"x as (select s._subject_id sid, {c('d', 'subject', 'deathdate')} d from subject s {lk('subject', 'deathdate', 'd', 's')} "
                f"  where {c('d', 'subject', 'deathdate')} is not null) "
                f"select count(*), min(m), max(m), sum(case when m < 0 then 1 else 0 end) from "
                f"(select (date_part('year', age(x.d, a.d)) * 12 + date_part('month', age(x.d, a.d)))::int m from a join x using (sid)) q")
    r, _ = run([{"condition": "COPD"}], {"time_between": {"from": {"condition": "COPD"}, "to": "death", "unit": "months"}})
    tb = r.get("time_between") or {}
    ap = tb.get("across_patients") or {}
    n, lo, hi, neg = truth[0]
    check(f"{tag} months from the first COPD diagnosis to death, as SQL", not r.get("error") and tb.get("patients_with_both") == n
          and ap.get("shortest") == lo and ap.get("longest") == hi and int(tb.get("negative") or 0) == int(neg or 0) and tb.get("to") == "death",
          f"error={r.get('error')} tool={tb.get('patients_with_both')} {ap} neg={tb.get('negative')} sql={truth[0]}")
    for spec, why in (({"from": "birth"}, "no 'to'"),
                      ({"from": "birth", "to": "income"}, "a field that is not a date"),
                      ({"from": "birth", "to": {"condition": "asthma"}, "unit": "weeks"}, "an unknown unit"),
                      ({"from": {"condition": "asthma", "which": "second"}, "to": "death"}, "'which' other than first or last")):
        r, _ = run(asthma, {"time_between": spec})
        check(f"{tag} refused: {why}", "error" in r and not (r.get("time_between") or {}).get("rows"), r.get("error", "")[:120])

    # --- counts over time
    tag = "[over time]"
    truth = sql(f"select extract(year from d)::int, count(*) from (select c._subject_id, min({c('t', 'condition', 'start')}) d from condition c "
                f"{lk('condition', 'description', 'l', 'c')} {lk('condition', 'start', 't', 'c')} "
                f"where lower({c('l', 'condition', 'description')}) in ('asthma', 'asthm') group by 1) x group by 1 order by 1")
    r, secs = run([], {"over_time": {"of": {"condition": "asthma"}, "first": True}})
    rows = (r.get("over_time") or {}).get("rows") or []
    check(f"{tag} new asthma patients per year (first: true), every year as SQL, in one call",
          [tuple(x) for x in rows] == truth and secs < 20, f"{secs:.1f}s tool={len(rows)} rows {rows[:3]} sql={len(truth)} rows {truth[:3]}")
    truth = sql(f"select extract(year from {c('t', 'encounter', 'start')})::int, count(distinct e._subject_id), count(distinct e._id) "
                f"from encounter e {lk('encounter', 'start', 't', 'e')} where e._subject_id in ({ASTHMA}) group by 1 order by 1")
    r, _ = run(asthma, {"over_time": {"of": "visit"}})
    rows = (r.get("over_time") or {}).get("rows") or []
    check(f"{tag} asthma patients' visits per year: patients and visits per year as SQL", [tuple(x) for x in rows] == truth,
          f"tool={len(rows)} rows {rows[-2:]} sql={len(truth)} rows {truth[-2:]}")
    truth = sql(f"select to_char({c('t', 'encounter', 'start')}, 'YYYY-MM'), count(distinct e._subject_id) from encounter e "
                f"{lk('encounter', 'start', 't', 'e')} where e._subject_id in ({HER}) group by 1 order by 1")
    r, _ = run(who, {"over_time": {"of": "visit", "by": "month"}})
    rows = (r.get("over_time") or {}).get("rows") or []
    check(f"{tag} her visits per month ('by': 'month')", [(x[0], x[1]) for x in rows] == truth, f"tool={rows[:3]} sql={truth[:3]}")

    # --- the age at each record
    tag = "[records age]"
    truth = sql(f"select {c('t', 'encounter', 'start')}, date_part('year', age({c('t', 'encounter', 'start')}, {c('b', 'subject', 'birthdate')}))::int "
                f"from subject s {lk('subject', 'birthdate', 'b', 's')} join encounter e on e._subject_id = s._subject_id "
                f"{lk('encounter', 'start', 't', 'e')} {lk('encounter', 'encounterclass', 'k', 'e')} "
                f"where {c('b', 'subject', 'birthdate')} = '{BORN}' and lower({c('k', 'encounter', 'encounterclass')}) = 'emergency' order by 1")
    r, _ = run(who, {"records": {"of": {"visit": "emergency"}, "fields": ["date", "age"]}})
    rec = r.get("records") or {}
    check(f"{tag} her emergency visits with her age at each, as SQL", rec.get("columns") == ["patient", "visit date", "age"]
          and [tuple(row[1:]) for row in rec.get("rows") or []] == truth, f"tool={rec.get('columns')} {rec.get('rows')} sql={truth}")

    # --- 'either' patients across different variables
    tag = "[either]"
    rich = (f"select s._subject_id from subject s {lk('subject', 'income', 'v', 's')} where {c('v', 'subject', 'income')} > 100000")
    union = f"select _subject_id from ({ASTHMA} union {rich}) u"
    truth = dict(sql(f"select upper({c('g', 'subject', 'gender')}), count(distinct s._subject_id) from subject s "
                     f"{lk('subject', 'gender', 'g', 's')} where s._subject_id in ({union}) group by 1"))
    n_union = sql(f"select count(*) from ({union}) x")[0][0]
    either = [{"condition": "asthma"}, {"field": "income", "op": ">", "value": 100000}]
    r, _ = run(either, {"breakdown": ["gender"]}, match="any")
    bd = r.get("breakdown") or {}
    got = {str(k).upper(): v for k, v in zip(bd.get("rows") or [], bd.get("row_totals") or [])}
    check(f"{tag} asthma OR income over 100,000, broken down by gender: exact, as SQL",
          not r.get("error") and r.get("count") == n_union and got == truth, f"error={r.get('error')} count={r.get('count')}/{n_union} tool={got} sql={truth}")
    truth = sql(f"select {c('i', 'subject', 'id')}, count(distinct e._id) from subject s {PID} "
                f"join encounter e on e._subject_id = s._subject_id where s._subject_id in ({union}) group by 1 order by 2 desc, 1")
    r, _ = run(either, {"per_patient": {"of": "visit", "n": 3}}, match="any")
    rows = (r.get("per_patient") or {}).get("rows") or []
    by_id = dict(truth)
    check(f"{tag} the most visits among those patients (per_patient over the union)",
          len(rows) == 3 and all(by_id.get(p) == n for p, n in rows) and [n for _, n in rows] == [n for _, n in truth[:3]]
          and (r.get("per_patient") or {}).get("patients") == len(truth), f"tool={rows} sql={truth[:3]}")
    r, _ = run(either, {"list": ["birthdate"], "limit": 5}, match="any")
    check(f"{tag} a list of them is still refused, with what can be asked instead", "error" in r and "per_patient" in r["error"], r.get("error", "")[:140])

    # --- who may
    tag = "[access]"
    PM.ensure_accounts()
    try:
        for show in ({"per_patient": {"of": "visit"}}, {"time_between": {"from": "birth", "to": {"condition": "asthma"}}},
                     {"over_time": {"of": "visit"}}):
            r, _ = run(asthma, show, user="perm_agg")
            key = next(iter(show))
            check(f"{tag} an aggregate-only account gets no {key}", "error" in r and not r.get(key), r.get("error", "")[:100])
        r, _ = run(who, {"per_patient": {"of": "visit", "by": "year"}}, user="perm_deid")
        check(f"{tag} a de-identified account gets figures per patient", not r.get("error") and (r.get("per_patient") or {}).get("rows"),
              r.get("error", "")[:100])
        r, _ = run(who, {"records": {"of": "visit", "fields": ["date", "age"]}}, user="perm_deid")
        check(f"{tag} and the age at each record", not r.get("error") and (r.get("records") or {}).get("rows"), r.get("error", "")[:100])
    finally:
        PM.remove_accounts()

    # --- the time limit says what was still running; a ranking's figures are not cohort counts
    tag = "[time limit]"
    from chiron_mcp import trace as T
    from chiron_mcp import webapp

    tr = T.Trace(question="which patient has the most systolic readings", dataset=DS, user="tester", thread="t", followup=False)
    tr.step("model", label="Model #1", status="ok")
    tr.step("tool", label="Asking Chiron")
    msg = webapp.timeout_message("4 minutes", tr.running())
    check(f"{tag} a Chiron query still running is named, and the advice is a narrower question",
          tr.running() == "tool" and "Chiron query was still running" in msg and "busy" not in msg, msg)
    tr2 = T.Trace(question="q", dataset=DS, user="tester", thread="t2", followup=False)
    tr2.step("model", label="Model #1")
    check(f"{tag} the model still writing is said as that", "model was still working" in webapp.timeout_message("4 minutes", tr2.running()),
          webapp.timeout_message("4 minutes", tr2.running()))
    tr3 = T.Trace(question="q", dataset=DS, user="tester", thread="t3", followup=False)
    tr3.step("tool", label="Asking Chiron", status="ok",
             output={"count": 10000, "per_patient": {"rows": [["a", 118], ["b", 117]], "across_patients": {"highest": 118},
                                                     "patients_in_group": 10000}})
    figs = T._record_figures(tr3)
    check(f"{tag} a ranking's figures are not taken for a wrong cohort count", 118 in figs and 117 in figs and 10000 not in figs, sorted(figs)[:6])

    # The live answer of 2026-11-17 led with the patient id; its 4335 was read as the lead
    # figure and the trace warned that the answer led with 4,335.
    lead = "The patient with the most systolic blood pressure values is **079305db-b81c-4335-7081-43bcc8b0b719**, with 118 readings."
    check(f"{tag} the digits of a patient id are not the answer's lead figure", webapp.headline_figure(lead) == 118,
          webapp.headline_figure(lead))
    check(f"{tag} nor a date's, nor a name's such as COVID-19",
          webapp.headline_figure("Her first visit was on 1930-12-14; she had 92 visits.") == 92
          and webapp.headline_figure("No patients have COVID-19.") is None
          and webapp.headline_figure("**445** of the 841 asthma patients are women.") == 445,
          [webapp.headline_figure(t) for t in ("Her first visit was on 1930-12-14; she had 92 visits.", "No patients have COVID-19.")])
    figs = [f["text"] for f in T._figures("is 079305db-b81c-4335-7081-43bcc8b0b719, with 118 readings; median 34, on 1930-12-14")]
    check(f"{tag} the trace's figure check reads the same way", figs == ["118", "34"], figs)

    print("ALL PASS" if not fails else f"{len(fails)} FAILED:\n  " + "\n  ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
