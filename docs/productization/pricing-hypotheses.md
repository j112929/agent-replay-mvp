# Pricing hypotheses

These proposals are not validated willingness to pay or active subscriptions.

| Plan | Proposed price/month | Configured test entitlements |
| --- | --- | --- |
| Starter | $299 | 3 projects, 5 seats, 1,000 imports, 100 case versions, 2,000 evaluations/month, 256 MB |
| Team | $799 | 10 projects, 15 seats, 5,000 imports, 500 case versions, 10,000 evaluations/month, 1 GB |
| Business | $1,999 | 20 projects, 30 seats, 10,000 imports, 1,000 case versions, 25,000 evaluations/month, 2 GB |

Do not sell unlimited model use or unlimited storage. Customer CI compute and model
API charges are excluded until measured and explicitly packaged. Adjust entitlement
configuration centrally in `saas/plans.json`, not throughout business logic. Projects,
seats and storage have continuous caps; per-project artifact safety caps also apply.
The original offline SDK slice retains operator caps unrelated to paid tiers. The
SaaS controller derives test subscription state only from verified provider data.

Validate $799 by paid pilot/continuation, measurable time saved and repeat usage.
Record objections about privacy, verifier accuracy, integration work, price and
budget owner. Discount only with an explicit end date; do not count a free pilot
as ARR. Stripe test-mode code is implemented and tested offline; live transactions require
operator approval. Never handle card data in this application.
