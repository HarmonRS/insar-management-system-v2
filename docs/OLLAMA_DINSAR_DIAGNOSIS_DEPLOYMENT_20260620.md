# Ollama D-InSAR Diagnosis Deployment

Last updated: 2026-06-20

This document is the current deployment contract for local Ollama integration in the D-InSAR analysis workflow.

## Scope

- Ollama is used only for D-InSAR diagnosis and map/image interpretation tasks.
- The visible UI entry is `InSAR形变分析 / D-InSAR / D-InSAR分析 / D-InSAR诊断`.
- The standalone `AI分析` first-level page is retired.
- Quality model training and batch quality prediction remain local backend tasks. They do not call Ollama.

## Configuration

Set these values in the backend environment:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_API_URL=http://127.0.0.1:11434/api/generate
DEFAULT_VLM_MODEL=qwen3-vl:30b
```

The backend reads them through `backend/app/config.py`.

`OLLAMA_BASE_URL` must point to the server-local Ollama service. Do not point it to UNC or a workstation share. `OLLAMA_API_URL` should normally be `${OLLAMA_BASE_URL}/api/generate`.

## Model Selection

`GET /ai/status` checks `${OLLAMA_BASE_URL}/api/tags` and returns:

- `ollama_online`
- `ollama_models`
- `ollama_vlm_models`
- `ollama_base_url`
- `default_vlm_model`

The D-InSAR diagnosis panel uses `ollama_vlm_models` for its model dropdown. `ollama_models` is the raw installed-model list and may include pure text models. If `DEFAULT_VLM_MODEL` is installed and classified as a vision model, it is selected. Otherwise the first detected vision model is selected.

The backend still has a fallback detector for compatibility. Preference order is:

1. User-selected model, if present in Ollama.
2. Model name containing `qwen3-vl`.
3. Model name containing `qwen2-vl`.
4. Model name containing `minicpm-v`.
5. Model name containing `llama3.2-vision` or `llava`.
6. Any model name containing `vl`, `vision`, or `llava`.
7. `DEFAULT_VLM_MODEL`.

## Runtime Flow

1. User opens `D-InSAR分析`.
2. User selects `D-InSAR诊断`.
3. Frontend calls `POST /ai/diagnosis`.
4. Backend creates an `AI_DIAGNOSIS` task and job.
5. Job handler reads the registered D-InSAR preview image, injects spatial context and quality context into the prompt, then calls Ollama `/api/generate`.
6. The diagnosis report is written to `ai_diagnosis`.
7. The panel lists diagnosis records from `GET /ai/diagnosis`.

The older `POST /ai/analyze-result/{result_id}` and `AI_ANALYZE` task remain compatibility code. New UI should use `POST /ai/diagnosis` and `AI_DIAGNOSIS`.

## Deployment Check

Run these checks on the server:

```powershell
ollama list
curl http://127.0.0.1:11434/api/tags
```

Then check the system endpoint:

```powershell
curl http://127.0.0.1:8000/api/ai/status
```

Expected result:

- `ollama_online` is `true`.
- `ollama_vlm_models` contains at least one vision-capable model.

Recommended model families for this project:

- `qwen3-vl`
- `qwen2-vl`
- `minicpm-v`
- `llama3.2-vision`
- `llava`

Avoid pure text models such as `qwen2`, `llama3`, `mistral`, or `gemma` for D-InSAR diagnosis.

## Failure Handling

- If the panel shows Ollama offline, verify `ollama serve` is running and the configured port matches `.env`.
- If diagnosis stays queued or fails quickly, inspect the task log for `AI_DIAGNOSIS`.
- If the model dropdown is empty, `/api/tags` is unreachable or Ollama has no models installed.
- If a selected model fails at generation time, confirm the model supports image input.
