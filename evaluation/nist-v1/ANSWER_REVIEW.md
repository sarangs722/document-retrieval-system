# Local LLM answer review

The predeclared sample contains the first answerable question and the unanswerable question for each of the seven test-topic documents: 14 stateless queries. It uses the development-selected hybrid mode and the unchanged local Qwen3 generation prompts. Actual streamed responses, citation snapshots, structural citation status, and timings are in `answer-audit.json`; exact model and prompt hashes are in `answer-environment.json`. All 14 audit conversations were deleted after capture. The source PDFs remain indexed.

This is a **coding-agent manual review**, not independent human adjudication or a general accuracy estimate. Citation status checks label syntax and membership. They do not evaluate the claims below. No automatic entailment score is assigned.

| Question | Requested fact / refusal | Review of actual claims and cited excerpts |
| --- | --- | --- |
| aes-01 | Correct key sizes | Adds a false claim that 4, 6, and 8 are round counts. The cited p34 passage identifies these as Nk values, not rounds; physical p12 gives 10, 12, and 14 rounds. Structural citation status was valid. |
| aes-06 | Clean refusal | Does not invent the GPU throughput measurement. An uncited refusal has structural status missing, which does not make the refusal wrong. |
| sha3-01 | Incorrect | Calls the KECCAK-p permutation family the two extendable-output functions. The expected names are SHAKE128 and SHAKE256. The cited explanation does not support its classification. Structural status was valid. |
| sha3-06 | Clean refusal | Does not invent an Apple M5 latency measurement. |
| modes-01 | Correct mode names | Cites label 5, whose bounded excerpt says there are five modes but does not name them. The names occur elsewhere in the source/candidate context. Correct content is not sufficient for this particular citation to support the enumeration. |
| modes-06 | Clean refusal with topical context | Gives general mode information, then correctly says CedarFin's database mode is not documented. The topical context is supported by its cited abstract. |
| gcm-01 | Correct value, insufficient cited support | Says 96 bits, citing an algorithm that handles both 96-bit and other IV lengths. That conditional algorithm does not establish the claimed restriction recommendation. The actual recommendation is on physical p16. Its additional documentation-responsibility claim is supported by p30. |
| gcm-06 | Mixed refusal with unsupported assertion | Initially asserts that HarborLedger's production key is specified in SP 800-38D, then admits the exact key is not provided. Neither cited excerpt mentions HarborLedger. Do not count this as a clean correct refusal. |
| ai-rmf-01 | Correct | The cited p7 excerpt explicitly describes the Framework as voluntary, rights-preserving, and non-sector-specific. |
| ai-rmf-06 | Clean refusal | Does not invent an organizational risk threshold. |
| authentication-01 | Correct | The cited p25 excerpt explicitly requires 15 characters for single-factor passwords. Repetitive wording is a quality issue, not an unsupported factual claim here. |
| authentication-06 | Clean refusal | Does not invent an account recovery code. |
| incident-response-01 | Correct central fact; some broad elaboration | Recover (RC) and restoring affected assets/operations are supported by p13. The added statement about minimizing impact is broader than the cited p9 framework discussion; strict claim-level review should flag that elaboration for stronger support. |
| incident-response-06 | Clean refusal | Does not invent a breach record count. |

Six of seven answerable responses contain the expected central fact; one misidentifies it. That observation hides important problems: a correct central fact can be followed by a false extra claim, cite the wrong bounded passage, or infer a recommendation from a conditional algorithm. Six negatives receive clean refusals; the seventh mixes refusal with an unsupported organizational assertion. These are observations about this small sample, not portfolio accuracy claims.

The next reliability work should evaluate every substantive claim against its cited excerpt, distinguish unsupported answers from supported refusals, and test evidence-sufficiency gating or a separately validated verifier. Keep verifier confidence and citation-label status separate. A stronger generator or a prompt change alone must be evaluated on new held-out questions; do not tune against these answers and relabel the resulting repeat as a new unbiased test.
