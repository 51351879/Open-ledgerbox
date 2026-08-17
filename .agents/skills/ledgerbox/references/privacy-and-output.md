# Privacy and output

Candidate facts necessarily enter the user's active Agent conversation. The local STDIO bridge does not promise that the chosen model is local or offline.

## During the run

- Use candidate data only through the five proposal tools.
- Do not copy descriptors, names, dates, amounts, currencies, account details, transaction IDs, run IDs, revision IDs, or per-row decisions into files, logs, source code, tests, issues, pull requests, or Cloud tasks.
- Do not request a PDF, archive, database, screenshot, browser snapshot, local manifest, credential, or repository-external path.
- Do not use model confidence as an approval rule.

## Final response

Use the contract's aggregate-only fixed shape. Never add a category breakdown or reproduce any descriptor or per-transaction decision. Never reveal a complete, truncated, or abbreviated transaction, run, group, or revision identifier; shortening a secret-bearing identifier is still disclosure.

Describe the negotiated effect exactly. V1 and v2 `review_first` are pending audits and must use `No effective category changed`. V2 `automatic` may be described only as atomically applied with Agent attribution and whole-run withdrawal; never include row-level contents.

If the user asks for row-level details in chat, direct them to the local Ledgerbox review area instead of repeating tool data.
