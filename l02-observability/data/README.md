# The data: real consumer complaints

`complaints.jsonl.gz` holds **8,000 complaints from the Consumer Complaint
Database of the US Consumer Financial Protection Bureau (CFPB)**, one JSON
object per line.

**Licence: public domain** (a work of the US federal government; published by
the CFPB under CC0 1.0, "no rights reserved").

## Where it comes from

- Consumers file complaints about financial products with the CFPB, which
  sends them to the company and publishes the result. Until August 2026 it
  also published the **narrative**, what the consumer wrote, when the
  consumer agreed and after removing personal details (shown as `XXXX`).
- **On 14 August 2026 the CFPB stopped publishing narratives.** Its bulk
  export no longer has them. These come from **the last export that did**,
  dated 11 August 2026, archived on Hugging Face as
  `vivekkopthsd/cfpb-consumer-complaints`.
- **Checked against the CFPB's current export** (downloaded 2026-11-07), by
  complaint id: all 8,000 are there, with the same product, issue, date and
  timeliness; the company's response matches for 7,999 (one was updated
  later).

## What was kept

- **Six products** a European consumer lender would recognise: credit or
  prepaid card, checking or savings account, money transfer, personal loan,
  vehicle loan or lease, debt collection.
- Complaints received from 24 April 2017 (when the CFPB's 2017 categories
  start) to 10 August 2026, drawn at random.
- **Dropped:** the company's name, the consumer's state and ZIP code.

| Field | What it is |
|---|---|
| `complaint_id` | the CFPB's id |
| `date_received` | when the CFPB received it |
| `product_as_filed` | the product the consumer chose, under the CFPB's names of the day |
| `product` | the same product under the CFPB's 2017 names: the platform's routing table uses these |
| `product_2023` | the same product under the names the CFPB introduced on 25 August 2023 |
| `sub_product`, `issue`, `sub_issue` | what the consumer chose further down |
| `narrative` | what the consumer wrote |
| `tags` | "Older American" and "Servicemember", when the consumer said so |
| `company_response` | how the real company closed it (explanation, relief, money back) |
| `timely_response` | whether the real company answered in time |

## Things to know before trusting it

- **The complaints were written to many US companies**, which the lab treats
  as complaints to one company. Some narratives name the real company.
- **The consumer chose the category**, not an expert: some are wrong, and the
  lab's "right answer" for triage is the consumer's choice.
- **The categories changed** in April 2017 and August 2023: the lab's incident
  I1 comes from the second change.
- **Narratives were published only with the consumer's consent**: the people
  who wrote them are a self-selected minority of all complainants.
