# NIST technical corpus: reference retrieval run

Completed: 2026-10-29T12:53:14.754053+00:00. Selected on development: **hybrid**.

Ten PDFs, 515 physical pages, 60 agent-authored questions. Development: 15 answerable + 3 unanswerable; test: 35 answerable + 7 unanswerable. All ten documents are retrieval candidates in both splits.

These are exact evidence-anchor hit rates, not generated-answer accuracy. Labels are not exhaustive or independently human-adjudicated. See ../../docs/BENCHMARK.md for protocol, provenance, and limitations.

| Split | Mode | Evidence hit@1 | Hit@3 | Hit@5 | MRR@5 | Negative empty results | Median / p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | keyword | 53.3% | 66.7% | 80.0% | 0.6189 | 0.0% | 195 / 305 |
| dev | vector | 60.0% | 73.3% | 73.3% | 0.6444 | 0.0% | 216 / 431 |
| dev | hybrid | 60.0% | 73.3% | 80.0% | 0.6689 | 0.0% | 218 / 330 |
| test | keyword | 45.7% | 77.1% | 80.0% | 0.6105 | 0.0% | 183 / 282 |
| test | vector | 25.7% | 40.0% | 54.3% | 0.3552 | 0.0% | 219 / 325 |
| test | hybrid | 28.6% | 65.7% | 77.1% | 0.4733 | 0.0% | 216 / 322 |

A nonempty search result for an unanswerable question is only a retrieval observation; no refusal or hallucination rate was measured.

The indexed corpus contains 1226 chunks. Questions without an intact anchor in any stored chunk: none.

## Test misses by mode

All missed answerable questions are retained below, including misses attributable to incomplete labels or chunking. Full ranked passages and component scores are in the JSON report.

| Mode | Question | Expected evidence pages | First returned document/page |
| --- | --- | --- | --- |
| keyword | aes-03 | aes p12 | aes p12 |
| keyword | sha3-01 | sha3 p3 | sha3 p4 |
| keyword | modes-01 | modes p5 | gcm p39 |
| keyword | modes-04 | modes p22 | modes p26 |
| keyword | gcm-01 | gcm p16 | gcm p33 |
| keyword | gcm-02 | gcm p16 | gcm p11 |
| keyword | authentication-02 | authentication p25 | authentication p22 |
| vector | aes-01 | aes p9 | aes p40 |
| vector | aes-02 | aes p9 | aes p19 |
| vector | aes-03 | aes p12 | aes p43 |
| vector | sha3-01 | sha3 p3 | sha3 p3 |
| vector | sha3-02 | sha3 p9 | sha2 p20 |
| vector | sha3-03 | sha3 p9 | sha3 p32 |
| vector | sha3-04 | sha3 p25 | sha3 p10 |
| vector | sha3-05 | sha3 p10 | sha3 p1 |
| vector | modes-02 | modes p17, modes p18 | modes p27 |
| vector | modes-05 | modes p24 | modes p43 |
| vector | gcm-01 | gcm p16 | gcm p34 |
| vector | ai-rmf-03 | ai-rmf p27 | ai-rmf p25 |
| vector | ai-rmf-04 | ai-rmf p30 | ai-rmf p25 |
| vector | authentication-01 | authentication p25 | authentication p88 |
| vector | authentication-04 | authentication p25 | authentication p27 |
| vector | incident-response-05 | incident-response p16 | incident-response p20 |
| hybrid | aes-01 | aes p9 | aes p34 |
| hybrid | aes-03 | aes p12 | aes p19 |
| hybrid | sha3-01 | sha3 p3 | sha3 p4 |
| hybrid | sha3-05 | sha3 p10 | sha3 p9 |
| hybrid | modes-04 | modes p22 | modes p26 |
| hybrid | gcm-01 | gcm p16 | gcm p30 |
| hybrid | ai-rmf-03 | ai-rmf p27 | ai-rmf p25 |
| hybrid | ai-rmf-04 | ai-rmf p30 | ai-rmf p25 |

## Reproducibility

- Questions SHA-256: `f6c7303bfb69edbe9ddc376d4c7a9c47bebbf2acf327b0966f46a3ad26e54567`
- Manifest SHA-256: `1b770cc8c5786ef3517c960ebf51369c4443f0afd8851c304a5fbc09f428a840`
- Pre-test selection SHA-256: `04c29a0688b23437d1658143e30458c84d7fce9d72fa00fab7c76fdbd2526ddd`
- Embeddings: BAAI/bge-small-en-v1.5; chunking: semantic.
- Source/model/chunk/retrieval settings, implementation hashes, and runner/lockfile hashes are recorded in the JSON artifacts.
- Sequential local timings include HTTP overhead and machine contention. This is not a load test.
- No generator or paid API was invoked for this run.
