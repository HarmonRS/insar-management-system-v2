# Repository Guidelines

## Agent Behavior
- Before modifying any code or files (including config/scripts), explicitly ask the user for permission.
- Do not change the database initialization/check/generation flow used by `start_system.bat` and `scripts/start_app.ps1`; any database-related changes must preserve that startup logic.
- Keep documentation (especially `docs/DEPLOYMENT.md`) updated when introducing new changes or features.
- Any newly written or updated Chinese text in code/config/docs must be saved with UTF-8 encoding (no mojibake/garbled characters).
- For map overlay architecture decisions, prioritize long-term runtime stability and smoothness over short-term implementation speed.
- For source radar preview overlay, prefer backend pre-corrected/georeferenced cache generation (Scheme B) and keep frontend rendering lightweight; avoid heavy per-frame frontend geometric transforms as the primary approach.

## Project Structure & Module Organization
- `backend/`: FastAPI app and services. Core entrypoint is `backend/app/main.py`, router in `backend/app/api.py`, and domain logic in `backend/app/services/`.
- `backend/migrations/`: SQL migrations (including spatial functions used at startup).
- `backend/Point/` and `backend/colormaps/`: spatial inputs and colormap assets used by the backend.
- `frontend/`: React + Vite app. Source lives in `frontend/src/`, public assets in `frontend/public/`, and production build output in `frontend/dist/`.
- `image_cache/`: generated thumbnails. Filenames use `ID_{id}_{name}.webp`.
- `scripts/`, `start_system.bat`, `run_backend.py`: local helper scripts.

## Build, Test, and Development Commands
- Python runtime: use Conda env `InSAR` only. Prefer `D:\anaconda3\Scripts\conda.exe run -n InSAR <command>`; do not call system `python`.
- Backend dev server: `cd backend && D:\anaconda3\Scripts\conda.exe run -n InSAR python -m uvicorn app.main:app --reload` (runs on port 8000).
- Alternate backend start: `D:\anaconda3\Scripts\conda.exe run -n InSAR python run_backend.py` (wrapper used by docs/scripts).
- Frontend dev server: `cd frontend && npm install && npm run dev` (runs on port 5173).
- Frontend build: `cd frontend && npm run build`.
- Frontend lint: `cd frontend && npm run lint`.

## Coding Style & Naming Conventions
- Python: follow PEP 8 (4-space indentation). Keep service logic in `backend/app/services/` and API routes in `backend/app/api.py`.
- Frontend: use 2-space indentation in `.jsx` and `.css` to match existing files; React components use `PascalCase` filenames (e.g., `StatisticsDashboard.jsx`).
- Image cache naming: `ID_{id}_{name}.webp` is required for cache lookups.
- Linting: ESLint is configured in `frontend/eslint.config.js` (run via `npm run lint`).

## Testing Guidelines
- No dedicated test framework or test directory is present in this workspace. If you add tests, document the runner and add a `test` script in `frontend/package.json` or a backend test command.
- Prefer naming tests `test_*.py` (pytest-style) or `*.test.jsx` if a JS test runner is introduced.

## Commit & Pull Request Guidelines
- Git metadata is not available in this workspace, so commit message conventions cannot be inferred. Use your team standard (e.g., Conventional Commits) and keep messages concise.
- PRs should include: a short description, linked issue/ticket if applicable, screenshots for UI changes, and clear testing notes.

## Configuration & Environment Notes
- Database is PostgreSQL with PostGIS. Set `DATABASE_URL` (format: `postgresql+asyncpg://user:pass@host/dbname`).
- IDL automation requires a local IDL/ENVI install (Windows) and is managed in `backend/app/idl_service.py`.
- Frontend expects backend at `http://localhost:8000`; update CORS in the backend if ports change.
- Conda environment for this workspace: `InSAR` (Python 3.10). On this machine, Conda executable is `D:\anaconda3\Scripts\conda.exe`.
- For `scripts/start_app.ps1`, prefer Conda-native startup config in `.env`:
  - `CONDA_EXE=D:\anaconda3\Scripts\conda.exe`
  - `CONDA_ENV_NAME=InSAR`
  - If `CONDA_ENV_NAME` is set, startup resolves the target env `python.exe` via Conda, then uses that interpreter for DB check/init/backend/worker.
