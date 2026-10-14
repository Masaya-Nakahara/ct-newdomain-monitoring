# ct-newdomain-monitoring

Code and prompts for the paper:

中原 匡哉：証明書透明性ログを用いた新規ドメイン監視のための実態計測 ―登録情報による新規性の検証と生成AIによる濫用の分離―
(Masaya Nakahara: Measuring Newly Observed Domains in Certificate Transparency Logs for Domain Monitoring: Registry-Based Newness Validation and Generative-AI-Based Abuse Separation)

## Contents / 構成
| Folder | 内容 |
|---|---|
| `collection/` | Daily sampling of crt.sh (latest up to 50,000 log entries per day), apex extraction from the subject Common Name, quality filters, and measurement of the observable volume in 14 time windows |
| `newness/` | Registration dates via RDAP (IANA bootstrap) and first-certificate dates via crt.sh |
| `leadtime/` | Random sample of Show HN posts (24 months, Algolia HN Search API), first certificates, RDAP, and first Wayback Machine captures (CDX API) |
| `classification/` | Fetching landing pages, keyword-based heuristic classifier, LLM classifiers (Codex CLI / llama.cpp) |
| `analysis/` | Robustness checks (day-level bootstrap, Mantel–Haenszel, Holm) and the blind manual verification of the random sample |
| `prompts/` | Full prompt (rubric), input format, output JSON schemas, and model settings |
| `results/` | Aggregated results reported in the paper (no domain-level data) |

## Data availability / データの公開範囲
Domain-level labels (manual or classifier output) and fetched page contents are **not** published:
classifier errors could wrongly name legitimate sites as abusive, and page contents belong to third parties.
They are available from the author upon reasonable request for research purposes.
The scripts expect a local SQLite database `ct_data.db` created by the collection scripts.

ドメインごとの判定（著者の目視・分類器の出力）と取得したページの本文は公開していません。
分類器の誤りにより正規のサイトを濫用サイトと名指しするおそれがあり，ページの本文は第三者の著作物であるためです。
研究目的で必要な場合は，著者までお問い合わせください。

## Requirements
Python 3.11+, `requests`, `pg8000` (crt.sh public PostgreSQL), `openpyxl`.
LLM classification requires the Codex CLI with a ChatGPT account, or llama.cpp with Qwen2.5-7B-Instruct (GGUF).

## License
MIT License (see `LICENSE`).
