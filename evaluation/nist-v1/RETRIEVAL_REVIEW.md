# Retrieval miss review

This is a coding-agent review of the eight hybrid test misses, performed after the reference run. It is qualitative, not independent human adjudication. The original labels, scores, selected mode, and retrieval parameters remain unchanged. All 50 answerable questions had an intact evidence anchor somewhere in the index; these misses do not indicate that the labels disappeared during ingestion.

| Question | Observation in the recorded top five | Interpretation |
| --- | --- | --- |
| aes-01 | Rank 1, AES physical p34, explicitly lists 128, 192, and 256 bits. The frozen anchor was on p9. | Valid alternative evidence was not labeled. |
| aes-03 | Rank 2, AES p12, ends at the definition of Nr; the values 10, 12, and 14 are in another chunk on that page. Rank 1 discusses rounds without giving their counts. | Correct page and topic, wrong chunk. The answer-bearing neighbor was not returned. |
| sha3-01 | Rank 3 returns the latter portion of the p3 abstract; the earlier portion names SHAKE128 and SHAKE256. Higher ranks include the title and general explanation. | Correct document, but introductory/title material displaced the answer-bearing chunk. |
| sha3-05 | Rank 5, SHA-3 p29, discusses distinguishing RawSHAKE inputs from SHA-3 hash inputs and future domains. It starts mid-explanation. | Related alternative support, but the two-bit-suffix context is fragmented. This is not treated as a complete automatic answer match. |
| modes-04 | Rank 2, SP 800-38A p25, explicitly requires unique counter blocks across all messages under a key. The frozen anchor was on p22. | Valid alternative evidence was not labeled. |
| gcm-01 | Rank 2, SP 800-38D p23, has separate algorithm branches for IV length equal to 96 and unequal to 96. It does not state the recommendation to restrict support to 96 bits, which is on p16. | Related numeric context does not entail the requested recommendation. |
| ai-rmf-03 | Rank 1, AI RMF p25, says governance is a cross-cutting function informing the other three functions. The frozen anchor was on p27. | Valid alternative evidence was not labeled. |
| ai-rmf-04 | Results discuss the four functions and MAP/MEASURE inputs to MANAGE; the explicit statement that MAP outcomes are the basis for both MEASURE and MANAGE was not returned. | Missing the specific dependency relationship asked about. |

Three misses clearly contain other valid answer passages. The unchanged 27/35 score therefore measures the frozen **anchor** labels and should not be described as exhaustive passage recall or answer accuracy. Do not retroactively convert this qualitative review into a higher held-out score.

Keyword retrieval has higher test MRR and hit@5 than the development-selected hybrid mode in this run. Small development groups, technical terms/numbers, repeated headings and front matter, and answer-bearing neighboring chunks merit investigation. Potential follow-up experiments include better passage judgments, neighboring-chunk context, front-matter handling, and reranking. Experiments motivated by these failures require a new held-out test version before claiming improvement.
