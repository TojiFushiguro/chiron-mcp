"""Load a starter set of team questions into a dataset's question library.

The set is for a Synthea dataset (synthea-10k, synthea-small, ...): questions a
researcher would ask, worded the way Ask answers well, filed under topics for the
Questions panel, four of them pinned to the new chat page. Each is added once: a team
question with the same words keeps its checks and its name, and gets the topic and the
pin it lacks.

    docker compose exec -T ask python /app/scripts/team_questions.py --dataset synthea-10k --owner <username>
    .venv/bin/python scripts/team_questions.py --dataset synthea-small --owner admin --dry-run

Run it where the Ask server runs (its container), so it writes the server's library file
(CHIRON_MCP_QUESTIONS_DB, or questions.sqlite in the state directory). Then press Check
all now on the Admin page's Team questions tab: every question is asked once, and its
first answer can be kept as the expected one.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# (topic, name, question, pinned)
SYNTHEA = [
    ("Patients overall", "All patients", "How many patients are in this dataset?", False),
    ("Patients overall", "By gender", "Break down all patients by gender", False),
    ("Patients overall", "By race", "How many patients are there of each race? Chart it", False),
    ("Patients overall", "By ethnicity", "How many patients are there of each ethnicity?", False),
    ("Patients overall", "By marital status", "How many patients are there of each marital status?", False),
    ("Patients overall", "Born before 1950", "How many patients were born before 1950?", False),
    ("Patients overall", "Deceased patients", "How many patients have died?", False),

    ("Conditions", "Type 2 diabetes", "How many patients have type 2 diabetes?", True),
    ("Conditions", "Top 10 conditions", "Which 10 conditions affect the most patients? Chart them", True),
    ("Conditions", "Hypertension and diabetes",
     "How many patients have both essential hypertension and type 2 diabetes?", False),
    ("Conditions", "Hypertension without diabetes",
     "How many patients have essential hypertension but not type 2 diabetes?", False),
    ("Conditions", "Women with obesity", "How many women have obesity?", False),

    ("Medications", "Top 10 medications", "What are the 10 most common medications by number of patients?", False),
    ("Medications", "Metformin in diabetes", "How many patients with type 2 diabetes take metformin?", False),
    ("Medications", "Albuterol in asthma", "How many patients with asthma take albuterol?", False),
    ("Medications", "Medications in hypertension",
     "What are the 5 most common medications among patients with essential hypertension?", False),

    ("Time between events", "Metformin within 90 days",
     "How many patients started metformin within 90 days after a type 2 diabetes diagnosis?", True),
    ("Time between events", "Emergency visit before asthma",
     "How many patients had an emergency visit within 30 days before an asthma diagnosis?", False),
    ("Time between events", "Hypertension after obesity",
     "How many patients were diagnosed with essential hypertension within a year after an obesity diagnosis?", False),

    ("Visits", "Visit types", "Which visit types are most common? Chart them", False),
    ("Visits", "Any emergency visit", "How many patients have had an emergency visit?", False),
    ("Visits", "Emergency visits in asthma", "How many patients with asthma have had an emergency visit?", False),
    ("Visits", "5 or more emergency visits", "How many patients had 5 or more emergency visits?", False),

    ("Over time", "Diabetes diagnoses per year", "Chart new type 2 diabetes diagnoses per year since 2000", True),
    ("Over time", "Emergency visits per year", "Chart the number of emergency visits per year since 2010", False),

    ("Breakdowns", "Diabetes by gender and race", "Break down patients with type 2 diabetes by gender and race", False),
    ("Breakdowns", "Asthma by age group",
     "How many patients with asthma are in each age group: under 18, 18 to 64, and 65 and over?", False),
    ("Breakdowns", "Income over $75,000", "How many patients earn more than $75,000 a year?", False),
    ("Breakdowns", "Diabetes and income", "How many patients with type 2 diabetes earn over $50,000?", False),

    ("Procedures", "Top 5 procedures", "What are the 5 most common procedures? Chart them", False),

    ("Patient lists", "Patients with obesity", "Show 10 patients with obesity, with their city and birthdate", False),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", required=True, help="the dataset's id, e.g. synthea-10k")
    ap.add_argument("--owner", required=True, help="the Chiron username recorded as having added them")
    ap.add_argument("--dry-run", action="store_true", help="list the questions, add nothing")
    args = ap.parse_args()

    from chiron_mcp import library

    if args.dry_run:
        for topic, name, text, pinned in SYNTHEA:
            print(f"{topic:22} {'pinned  ' if pinned else '        '}{name}: {text}")
        print(f"{len(SYNTHEA)} questions, {sum(p for *_, p in SYNTHEA)} pinned (dry run, nothing added)")
        return 0
    if not library.enabled():
        sys.exit("The question library is turned off here (CHIRON_MCP_QUESTIONS_DB=off).")
    before = {q["id"] for q in library.team(args.dataset, retired=True)}
    added = kept = 0
    for topic, name, text, pinned in SYNTHEA:
        q = library.add_team(args.owner, args.dataset, text, name, pinned=pinned, topic=topic)
        if q["id"] in before:
            kept += 1
        else:
            added += 1
    pinned = sum(1 for q in library.team(args.dataset) if q["pinned"])
    print(f"{args.dataset}: {added} added, {kept} already there; "
          f"{len(library.team(args.dataset))} active team questions, {pinned} pinned ({library.path()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
