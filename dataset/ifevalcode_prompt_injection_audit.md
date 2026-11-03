# IFEvalCode Dataset Security Audit

## Processing
- Source: `dataset/Multilingual-Multimodal-NLP-if-eval-code/default/0.0.0/7d2918680c806a235beed1224b2dc726a6771021/if_eval_code-testset-train.arrow`
- Sanitized copy: `dataset/ifevalcode_cleaned.jsonl`
- Rows retained: 810; rows removed: 0
- Removed field: `chinese_question` (the Chinese-language companion question)
- The original Arrow file was not modified.
- All text fields (including correctness/instruction checks and the removed Chinese question) were scanned as data.
- No dataset-provided code was executed.

## Prompt-Injection Scan
The scan uses high-signal text patterns. It can flag benign quoted examples and cannot prove a dataset is safe.
- Findings: 0
- No configured prompt-injection indicators were detected.
