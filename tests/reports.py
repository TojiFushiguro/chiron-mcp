"""PDF reports written in an answer: the answering rule, the trace's audit of each
```report block, and the server's tidying of answers keeping a report's JSON whole.

Offline: no Chiron, no database, no model.

    .venv/bin/python tests/reports.py

The page side (reading a report, its card, the PDF itself) is tests/reports_check.html.
Exit code 0 means every check held.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CHIRON_MCP_USERNAME", "tester")
os.environ.setdefault("CHIRON_MCP_USAGE_DB", "off")

from chiron_mcp import trace as T  # noqa: E402
from chiron_mcp import webapp  # noqa: E402

fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}  {str(detail)[:160]}")
    if not cond:
        fails.append(label)


CHART = {"type": "bar", "title": "Patients by age group", "label": "Patients",
         "data": [{"x": "18-49", "y": 120}, {"x": "50-79", "y": 410}, {"x": "80+", "y": 156}]}
GOOD = {
    "title": "Type 2 diabetes in Synthea 10k",
    "summary": "686 of the 10,000 patients have type 2 diabetes.",
    "numbers": [{"value": "686", "label": "patients with type 2 diabetes"}, {"value": 179, "label": "on metformin"}],
    "sections": [
        {"heading": "Who has it", "text": "Most are 50 to 79.", "chart": CHART, "table": True},
        {"heading": "Treatment", "text": "Metformin first.", "callout": {"value": "179", "text": "started metformin"}},
        {"heading": "By year", "table": {"columns": ["Year", "Patients"], "rows": [["2021", 12], ["2022", 15]]}},
    ],
    "about": {"looked_up": ["How many patients have type 2 diabetes?"], "counted": ["Once per patient"]},
}


def block(spec) -> str:
    body = spec if isinstance(spec, str) else json.dumps(spec, indent=1)
    return f"**686** patients have type 2 diabetes.\n\n```report\n{body}\n```"


def audit(spec):
    out = T.report_audit(block(spec))
    return out[0] if out else None


def main() -> int:
    # The rule is in both prompts, and in the one in use.
    for name in ("SYSTEM_PROMPT_FULL", "SYSTEM_PROMPT_SHORT"):
        sp = getattr(webapp, name)
        start = sp.find("When asked for a PDF, or a report to read or download")
        rule = sp[start: sp.find("\n- ", start)]
        flat = " ".join(rule.split())
        check(f"{name} has the report rule", start > 0 and "`report` holding JSON" in flat, rule[:120])
        check(f"{name}: exactly what was asked for, nothing else",
              "Put in exactly what the person asked for and nothing else" in rule and "one section for each thing" in rule)
        check(f"{name}: extras only when asked", "Never add these on your own" in flat and '"summary"' in rule and '"numbers"' in rule
              and '"callout"' in rule and '"about"' in rule)
        check(f"{name}: a change is a new report from the last one", "To change a report" in rule and "from the last one" in rule
              and "say in one sentence what changed" in rule)
        check(f"{name} limits sections", "At most 8 sections" in rule)
        check(f"{name} keeps patient lists to when asked", "patients one per row only when asked to" in " ".join(rule.split()))
        check(f"{name} keeps chart blocks out of a report's answer", "No separate chart" in rule)
        check(f"{name}: a bare request gets one section", "gets one section with the main count" in " ".join(rule.split()))
        check(f"{name}: a list gets its rows written out", "A list (patients, records) gets a table with every row written out" in flat
              and '"table": true is never a list' in flat)
        check(f"{name}: 'make that a PDF' carries the answer before it, a saved report its rows",
              "\"Make that a PDF\" or \"also a PDF\" asks for what the answer before it gave" in flat
              and "the patients one per row with that report's columns" in flat and "follows nothing" in flat)
        check(f"{name}: saving a report in Chiron is not a PDF", "saving a report in Chiron is" in " ".join(rule.split()))
        check(f"{name}: follow-ups after a PDF offer parts to add", "After a PDF report, instead, each of the three starts with \"Add\"" in " ".join(sp.split()))
        example = rule[rule.find("{"): rule.find("The title names")].strip().replace("{<a chart as above>}", "{}")
        try:
            spec = json.loads(example)
            parsed = True
        except ValueError as exc:
            spec, parsed = None, exc
        check(f"{name}: the example report is valid JSON (with a chart filled in)", parsed is True, parsed)
        check(f"{name}: the example has no extras", spec is not None and set(spec) == {"title", "sections"}, spec and list(spec))
        check(f"{name}: the example's title has no dataset name", spec is not None and "Synthea" not in spec["title"], spec and spec.get("title"))
        check(f"{name}: the title names what was asked, in the person's words", "The title names what this request asked for" in flat
              and "own words" in flat and "never the dataset's name" in flat and "retitles it" in flat)
    check("the prompt in use is one of the two", webapp.SYSTEM_PROMPT in (webapp.SYSTEM_PROMPT_FULL, webapp.SYSTEM_PROMPT_SHORT))

    # The audit: a good report.
    a = audit(GOOD)
    check("a good report reads", a and a["ok"] and a["spec"]["title"] == GOOD["title"], a)
    check("a good report has no issues", a and a["issues"] == [], a and a["issues"])
    check("no report block, no audit", T.report_audit("**5** patients.\n\n```chart\n{}\n```") == [])
    two = T.report_audit(block(GOOD) + "\n\n" + block({"title": "B", "summary": "x"}))
    check("each report block is audited", len(two) == 2 and two[1]["spec"]["title"] == "B", len(two))

    # What makes a report unreadable.
    a = audit("{\"title\": \"Broken\", \"sections\": [")
    check("invalid JSON: not ok, says why", a and not a["ok"] and "Not valid JSON" in a["issues"][0], a)
    check("invalid JSON keeps its source for the trace", a and a["source"].startswith("{\"title\""), a)
    a = audit("[1, 2]")
    check("a list is not a report", a and not a["ok"] and "not a JSON object" in a["issues"][0], a)
    a = audit({"title": "Nothing in it"})
    check("nothing to put in the PDF: not ok", a and not a["ok"] and any("nothing to put" in x for x in a["issues"]), a)
    a = audit({"title": "Only a summary", "summary": "686 patients."})
    check("a summary alone is a report", a and a["ok"], a)
    a = audit({"title": "Only numbers", "numbers": [{"value": "686", "label": "patients"}]})
    check("numbers alone are a report", a and a["ok"], a)

    # What is only worth a note.
    a = audit({"summary": "x", "sections": [{"heading": "A"}]})
    check("no title: a note, still ok", a and a["ok"] and "No title." in a["issues"], a)
    a = audit({"title": "t", "numbers": [{"value": str(i), "label": "n"} for i in range(6)]})
    check("more than 4 numbers: noted", a and any("6 key numbers" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": f"S{i}"} for i in range(10)]})
    check("more than 8 sections: noted", a and any("10 sections" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": ["just text", {"text": "no heading"}]})
    check("a section that is not an object: noted", a and any("Section 1 is not a JSON object" in x for x in a["issues"]), a and a["issues"])
    check("a section without a heading: noted", a and any("Section 2 has no heading" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "chart": {"type": "bar", "data": []}}]})
    check("a section's chart is checked as a chart", a and any(x.startswith("Section 1 chart:") for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "table": {"columns": ["a", "b"], "rows": [["1"]]}}]})
    check("table rows that do not match the columns: noted", a and any("do not match its 2 columns" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "table": {"columns": ["a"]}}]})
    check("a table without rows: noted", a and any("needs columns and rows" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "table": {"columns": ["a"], "rows": []}}]})
    check("an empty table: noted", a and any("has no rows" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "table": {"columns": ["a"], "rows": [[i] for i in range(1500)]}}]})
    check("a very long table: noted", a and any("1,500 rows; the PDF shows the first 1,000" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "table": True}]})
    check("table: true with no chart: noted", a and any("has no chart" in x for x in a["issues"]), a and a["issues"])
    a = audit({"title": "t", "sections": [{"heading": "A", "text": "x", "about": {"counted": ["c"]}}]})
    check('"about" inside a section: noted, still ok', a and a["ok"] and any('has "about", which belongs at the top level' in x for x in a["issues"]), a and a["issues"])

    # The trace: the summary line and the checks.
    answer = block(GOOD)
    tr = T.Trace(question="Make me a PDF report on type 2 diabetes", dataset="ds", user="alice",
                 thread="thread-r", followup=False, redact=webapp._redact)
    tr.post("charts", T.chart_audit(answer))
    tr.post("reports", T.report_audit(answer))
    tr.answer(answer)
    tr.finish(0, "")
    snap = tr.snapshot()
    check("the trace keeps the report", snap["post"]["reports"][0]["spec"]["title"] == GOOD["title"])
    check("the report's chart is not counted as a chart block", not snap["post"].get("charts"), snap["post"].get("charts"))
    tr2 = T.Trace(question="q", dataset="ds", user="alice", thread="thread-s", followup=False, redact=webapp._redact)
    bad = block("{\"title\": \"x\", ")
    tr2.post("reports", T.report_audit(bad))
    tr2.answer(bad)
    tr2.finish(0, "")
    titles = [c["title"] for c in tr2.snapshot()["checks"]]
    check("an unreadable report is a warning in the checks", any(t.startswith("Report: Not valid JSON") for t in titles), titles)
    lv = [c for c in tr2.snapshot()["checks"] if c["title"].startswith("Report: ")]
    check("... at warning level", lv and lv[0].get("level") == "warn", lv)
    a = audit('{"title": "Wrapped", "summary": "686 of the\n  10,000 patients"}')
    check("a string broken across lines is read as the page reads it", a and a["ok"] and a["spec"]["summary"] == "686 of the 10,000 patients", a)
    check("... with a note", a and any("breaks across lines" in x for x in a["issues"]), a and a["issues"])

    # tidy() must leave a report's JSON whole: links into Chiron, emails and local paths
    # inside its strings are cut without taking a quote with them.
    base = webapp.CONFIG.ui_url.rstrip("/") or "http://localhost:8000"
    spec = {"title": "Report", "summary": f"See {base}/query/12 and ops@example.org or /Users/someone/x.txt.",
            "sections": [{"heading": "Links", "text": f"{base}/results/9", "table": {"columns": ["a"], "rows": [[f"{base}/query/3"]]}}]}
    raw = "**5** patients.\n\n```report\n" + json.dumps(spec, indent=1) + "\n```"
    out = webapp.tidy(raw)
    body = out[out.find("```report") + 9: out.rfind("```")]
    try:
        kept = json.loads(body)
        ok = True
    except ValueError as exc:
        kept, ok = None, exc
    check("tidy keeps a report's JSON valid", ok is True, ok)
    if kept and webapp.CONFIG.ui_url:
        check("... and still takes Chiron links out of it", base not in json.dumps(kept), kept)
    check("... and redacts emails in it", kept and "ops@example.org" not in json.dumps(kept), kept)
    check("... and drops local paths in it", kept and "/Users/someone" not in json.dumps(kept), kept)
    one = json.dumps(spec)  # all on one line
    out1 = webapp.tidy("```report\n" + one + "\n```")
    try:
        json.loads(out1[out1.find("```report") + 9: out1.rfind("```")])
        ok1 = True
    except ValueError as exc:
        ok1 = exc
    check("tidy keeps a one-line report's JSON valid", ok1 is True, ok1)
    prose = webapp.tidy(f"Open [the cohort]({base}/query/5) or {base}/query/6 here.")
    if webapp.CONFIG.ui_url:
        check("tidy still takes Chiron links out of prose", base not in prose, prose)

    # Open shows a PDF through a service worker, so the viewer saves it under its title: the
    # server serves the worker's script as JavaScript, from the page's own folder.
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/pdf-sw.js", timeout=10) as r:
            body, ctype = r.read().decode(), r.headers.get("Content-Type", "")
        check("the PDF worker is served as JavaScript", "javascript" in ctype and "self.addEventListener('fetch'" in body, ctype)
        check("... it answers only for PDFs it was given, and keeps them in memory", "FILES.get(url.pathname)" in body and "caches" not in body)
    finally:
        srv.shutdown()
    page = (webapp.WEBUI / "index.html").read_text()
    check("the page registers the worker for files/ only", "register('pdf-sw.js', { scope: 'files/' })" in page)

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
