# Transfer boundaries

A transfer-kind proposal means the transaction moves the user's own money between owned accounts, represents owned-account principal moving in or out, or records an explicitly identified financing principal flow. It changes cash-flow reporting when applied, so require stronger evidence than for an ordinary category.

## Evidence that can support a proposal

- wording that explicitly connects the movement to the user's own checking, savings, brokerage, or credit-card account;
- an explicit card-payment or owned-account repayment mechanism;
- an explicit principal contribution to or withdrawal from an owned investment account;
- an explicit installment-financing disbursement or repayment, while keeping separately identified fees ordinary.

Transfer and ordinary groups use the same negotiated Core boundary. Schema v1 and v2 `review_first` remain pending; v2 `automatic` applies both kinds atomically with Agent provenance. The mode never weakens the evidence requirement.

## Evidence that is not enough

- a payment rail, peer-to-peer network, wire, ACH, autopay, or the word “transfer” by itself;
- a platform name without ownership, purpose, or principal evidence;
- an incoming deposit with no source evidence;
- an outgoing payment whose destination might be another person or a merchant;
- similar wording on two rows without proof that they are the two sides of the user's own movement.

Do not infer ownership from direction, amount similarity, timing, or familiarity with a brand. Do not pair or reconcile transactions; the proposal tools expose classification candidates, not account-ownership proof.

Cash withdrawal and cash deposit rows are not automatically one self-transfer. When the available facts do not establish ownership and purpose, omit the candidate for human review.
