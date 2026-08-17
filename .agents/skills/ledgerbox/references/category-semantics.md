# Category semantics

Fetch category IDs, labels, and kinds at runtime from `ledgerbox_categories`. Never memorize a category count, invent an ID, or maintain a second taxonomy inside the Skill.

## Evidence order

Use evidence in this order:

1. explicit economic event or service;
2. explicit counterparty function;
3. direction and returned category kind;
4. generic merchant, platform, or payment-rail wording.

Earlier evidence outweighs later evidence. Direction is a constraint, not enough evidence by itself. A credit is not automatically income, and a debit is not automatically an expense.

## Choosing among returned categories

- Select the most specific returned category whose meaning is supported by the descriptor and direction.
- Distinguish a fee from the purchase, repayment, or principal movement that caused it.
- Treat explicit payroll-like compensation differently from a generic deposit.
- Treat explicit interest, reward, refund, or cash-deposit language according to its economic event only when a compatible returned category exists.
- Treat an investment platform name as insufficient to distinguish principal, proceeds, fees, rewards, or purchases.
- Treat a retailer that sells many kinds of goods as weak evidence unless the transaction text identifies the purchased service or product class.
- Do not create a near-match when the taxonomy lacks a supported category. Omit the candidate; taxonomy-gap triage is a separate workflow.

The goal is a defensible proposal, not maximum coverage. A returned category's label and kind describe the current product taxonomy; they do not grant permission to rewrite ledger facts.
