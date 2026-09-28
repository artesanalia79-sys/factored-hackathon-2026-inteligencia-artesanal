# Known limitations

Honest list of what the system does not do or cannot claim. Keep it current; judges read it.

| Area | Limitation | Mitigation / status |
|---|---|---|
| Regulation | Only Argentina (Ley 25.065 arts. 26-29) was checked against a primary source. MX and CO dispute rules are labeled synthetic. | A human must verify MX/CO before any real use (Task 9). |
| Identity | Login is a persona picker with a mock OTP; no real KYC. | Out of scope for the hackathon; the session model is production-shaped. |
| Data | The public demo runs on team-generated synthetic personas; organizer-derived data is only used locally. | `DATA_MODE=curated` is local only (Task 19). |
| Fraud | `is_fraud` is never used at runtime; `fraud_score` threshold is provisional. | ADR 0003, Task 5. |
| Evaluation | Scripted users are simpler than real customers. | Held-out set with dialect quotas, red team and a small pilot (Tasks 17, 24, 25). |
