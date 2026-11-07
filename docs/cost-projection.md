# Cost per onboarding and monthly projection (PRD 17)

Cost is measured, not estimated. Every model call writes a `cost_events` row with its tokens and
INR price, and every completed run gets an `infra_alloc` row. The console dashboard and
`GET /api/v1/metrics/summary` read these rows. This page records what the seed run measured,
and how to turn that into a monthly figure once TBO's volumes (issue #19) and a model choice
(issue #20) are known.

## Formula

```
cost_run     = Σ llm(tokens_in × p_in + tokens_out × p_out) + Σ cpu_seconds × p_cpu + infra_alloc
infra_alloc  = monthly fixed infra (config/infra_costs.yaml) ÷ projected monthly runs
monthly_inr  = Σ_workflow  volume(workflow) × variable cost per run(workflow)  +  monthly fixed infra
```

`monthly_projection()` in `relay/core/cost/metrics.py` computes the last line. It returns nothing
until every workflow has a volume in `config/volumes.yaml`, because a partial sum would look like
a real figure while leaving out part of the bill.

## What the seed run measured

All 37 PRD 22 seed candidates went through `onboarding.v1` on the real engine, rules and
connectors, with skills on the offline `mock` provider. 34 runs completed, 2 are waiting on a
joiner who never uploads, and 1 was refused for an unapproved offer. 98.75% of finished steps
needed no human.

| Measure (34 completed onboardings) | Total | Per onboarding |
|---|---|---|
| Model calls | 43 | 1.26 |
| Tokens in | 6,510 | 191 |
| Tokens out | 686 | 20 |
| OCR CPU seconds | 0 (text-layer documents) | 0 |

Where the calls came from:

| Step | Calls | Avg tokens in / out | Why a model is called |
|---|---|---|---|
| `draft_manager_brief` | 34 | 146 / 16 | Rewording a template; capped at 150 tokens |
| `cross_check` (exception analyst) | 5 | 193 / 20 | Summarising a conflict for the person who decides it |
| `extract_documents` (low confidence) | 3 | 138 / 7 | Second look at a blurry document |
| `order_laptop` (exception analyst) | 1 | 182 / 20 | Summarising the asset outage |

Most of the work needs no model at all. Dates, notice periods, laptop specs, routing and
eligibility are rules (PRD 6.8). Connectors are plain HTTP. Documents with a text layer are read
without OCR or a model. The model is used only where wording or judgement helps, and it is capped.

## Turning tokens into rupees

Per onboarding, the variable model cost is:

```
191 × p_in / 1000  +  20 × p_out / 1000      (INR, with p_in and p_out in INR per 1k tokens)
```

At the offline `mock` provider's placeholder prices (₹0.01 in, ₹0.03 out per 1k tokens), the seed
run cost **₹0.0847 in total, about ₹0.0025 per onboarding**. Those prices are placeholders; the
real ones go in `config/models.yaml` once the evals pick a model, and the dashboard then shows real
rupees with no code change.

For any price, it helps to see how the figure grows:

| If tier1 costs (INR per 1k tokens, in / out) | Model cost per onboarding |
|---|---|
| p_in / p_out | 0.191 × p_in + 0.020 × p_out |
| 1 / 4 | ₹0.27 |
| 5 / 20 | ₹1.36 |
| 25 / 100 | ₹6.78 |

These rows are worked examples of the formula, not quotes for any provider.

### When documents are real scans

The seed documents carry a text layer, so extraction costs nothing. With real scans, PaddleOCR
runs on CPU (`ocr_cpu` cost events, priced per CPU second in `config/infra_costs.yaml`). The
extractor calls tier1 only when OCR confidence is below its threshold, and tier2 only below that.
The eval harness (`evals/threshold_report.md`) sets those thresholds to minimise expected cost,
counting the cost of an error and of an HR person's time.

## Monthly projection

```
monthly_inr = onboardings_per_month × variable cost per onboarding
            + exits_per_month × variable cost per exit
            + monthly fixed infra
```

Fill in `config/volumes.yaml` (onboarding and exit volumes, from TBO) and
`config/infra_costs.yaml` (VM, Postgres, storage, egress). The dashboard's monthly projection
appears as soon as both are set. Exit runs, as built, make one model call each (the knowledge
transfer plan), so their variable cost is close to one manager brief.

## Reproduce

```bash
uv run relay seed load
uv run relay simulate
```

Then read the dashboard, or query the measurements directly:

```sql
select r.definition_name, count(distinct r.id) as runs, count(ce.id) as calls,
       sum(ce.tokens_in) as tokens_in, sum(ce.tokens_out) as tokens_out, sum(ce.cost_inr) as inr
from workflow_runs r
left join cost_events ce on ce.run_id = r.id and ce.kind = 'llm'
where r.status = 'completed'
group by 1;
```
