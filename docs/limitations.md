# Known limitations

Honest list of what the system does not do or cannot claim. Keep it current; judges read it.

| Area | Limitation | Mitigation / status |
|---|---|---|
| Regulation | Only Argentina (Ley 25.065 arts. 26-29) was checked against a primary source. MX and CO dispute rules are labeled synthetic. | A human must verify MX/CO before any real use (Task 9). |
| Identity | Login is a persona picker with a mock OTP; no real KYC. | Out of scope for the hackathon; the session model is production-shaped. |
| Data | The public demo runs on team-generated synthetic personas; organizer-derived data is only used locally. | `DATA_MODE=curated` is local only (Task 19). |
| Fraud | `is_fraud` is never used at runtime. `fraud_score >= 30` escalates with offline precision 0.80 and recall 0.55; the score separates the label almost perfectly (a synthetic-generator artefact), so real-world precision would be lower, and ~21% of fraud rows have no score. | ADR 0003 (accepted), `docs/evidence/fraud_score_thresholds.md`. |
| Privacy | Silver `customers.document_hash` is an unsalted sha256 of a short numeric identity document (8-10 digits), so it can be brute-forced back to the number; it is fine only while silver stays offline. | If it ever leaves offline use (serving, auth lookups), switch to an HMAC keyed from `.env` with the same trim normalization on the auth side. Owners: T3 (contracts), T7 (auth). |
| Evaluation | Scripted users are simpler than real customers. | Held-out set with dialect quotas, red team and a small pilot (Tasks 17, 24, 25). |
