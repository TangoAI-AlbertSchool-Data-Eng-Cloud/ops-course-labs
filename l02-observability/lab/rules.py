"""Triage without the LLM: keyword rules, the fallback at degradation level 3.

Cruder than the LLM (see the lab README for how often it is right), but it
needs no provider, costs nothing and never times out. Rules are checked in
order; the first that matches decides.
"""

import re

# (pattern, product): the words a consumer is likely to use for each product.
# Order matters: "car loan" must hit vehicle loans before plain "loan" does,
# and "debt" must be checked before the card and account words it sits beside.
RULES = [
    (r"\b(debts?|collect\w*|collector|fdcpa|validation|owe[ds]?)\b", "Debt collection"),
    (r"\b(car|vehicle|auto|truck|lease|repossess\w*|dealership)\b", "Vehicle loan or lease"),
    (r"\b(payday|title loan|personal loan|installment loan|advance loan|loan|lender)\b",
     "Payday loan, title loan, or personal loan"),
    (r"\b(paypal|venmo|zelle|cash app|wire transfer|money transfer|western union|crypto\w*|bitcoin|coinbase|wallet|remittance|transfer|sent money)\b",
     "Money transfer, virtual currency, or money service"),
    (r"\b(credit card|prepaid|card|statement balance|apr)\b", "Credit card or prepaid card"),
    (r"\b(checking|savings|overdraft|deposit|account)\b", "Checking or savings account"),
]
COMPILED = [(re.compile(pattern, re.IGNORECASE), product) for pattern, product in RULES]


def classify(narrative: str) -> str:
    """The first product whose words appear; checking accounts by default."""
    for pattern, product in COMPILED:
        if pattern.search(narrative):
            return product
    return "Checking or savings account"   # the commonest catch-all
