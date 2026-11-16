"""The complaints: real ones, from the US Consumer Financial Protection Bureau.

8,000 complaints written by consumers about six financial products, each with
the category the consumer chose when filing it (see data/README.md). The lab
sends them to the platform as if customers had just written them.
"""

import gzip
import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "complaints.jsonl.gz"

# The six products, under the names the CFPB used from April 2017 to August
# 2023. The platform's routing table was built on these names: one team each.
PRODUCTS = [
    "Credit card or prepaid card",
    "Checking or savings account",
    "Money transfer, virtual currency, or money service",
    "Payday loan, title loan, or personal loan",
    "Vehicle loan or lease",
    "Debt collection",
]

# On 25 August 2023 the CFPB renamed some of its categories. These are the six
# products under the new names: cards split in two, personal loans renamed,
# the other four unchanged. Incident I1 is a prompt that adopts them.
PRODUCTS_2023 = [
    "Credit card",
    "Prepaid card",
    "Checking or savings account",
    "Money transfer, virtual currency, or money service",
    "Payday loan, title loan, personal loan, or advance loan",
    "Vehicle loan or lease",
    "Debt collection",
]

# Which team handles which product. Keyed by the 2017 names: a product name
# that is not in this table reaches no team and waits in "unassigned".
ROUTING = {
    "Credit card or prepaid card": "cards",
    "Checking or savings account": "accounts",
    "Money transfer, virtual currency, or money service": "payments",
    "Payday loan, title loan, or personal loan": "loans",
    "Vehicle loan or lease": "vehicle-loans",
    "Debt collection": "collections",
}
TEAMS = sorted(set(ROUTING.values()))

# The teams whose products are payment services, the only ones the 15-day rule
# binds (PSD2 Article 101(2); Annex I lists the services it covers): cards are
# payment instruments, checking accounts are payment accounts, and money
# transfers are remittance. Loans and debt collection fall under other rules.
PSD2_TEAMS = {"cards", "accounts", "payments"}


def load_complaints() -> list[dict]:
    """Every complaint of the sample, oldest first."""
    with gzip.open(DATA_FILE, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def is_vulnerable(complaint: dict) -> bool:
    """The CFPB's tags mark older consumers and service members."""
    return bool(complaint.get("tags"))
