# Prompts / プロンプト

## Rubric (shared) / 共通の判定基準
`rubric_7class.txt` (zero-shot, no examples). The 7 classes are mapped to 3 classes:
service, commerce, business, blog, unknown → **live**; parking → **non-content**; spam → **abuse**.
Unreachable pages or pages with empty text are treated as non-content without calling the LLM.

## Input / 入力
Each domain is given as JSON: `{"apex": ..., "title": first 120 chars, "meta": first 200 chars, "text": first 900 chars}`.

## Commercial LLMs via Codex CLI (gpt-5.3-codex-spark, gpt-5.5)
Prompt = rubric + `\n\nClassify EVERY domain. Return {"results":[{apex,category}]}.\n\n` + JSON array of 25 domains.

```
codex exec -m <MODEL> -c model_reasoning_effort=low -s read-only --skip-git-repo-check --ephemeral \
           --output-schema schema_batch.json -o out.json -
```
Temperature and seed cannot be set through Codex CLI. gpt-5.3-codex-spark became unavailable through this route in October 2026.

## Open-weight model (Qwen2.5-7B-Instruct Q4_K_M, llama.cpp b11342, CPU)
Prompt = rubric + `\n\nClassify this ONE domain. Respond only with JSON {"category": ...}.\n\n` + JSON of one domain.
Server: `llama-server -m <gguf> -c 4096 --temp 0 --seed 42`; request: `temperature 0, seed 42, max_tokens 20, response_format json_schema` (`schema_single.json`).
