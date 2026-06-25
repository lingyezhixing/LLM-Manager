# LLM-Manager WebUI

React + Vite + TypeScript + Tailwind v4 + TanStack Query. Served by the FastAPI
backend in production (no Node at runtime).

## Develop
```bash
npm install
npm run dev          # Vite dev server, proxies /api + /v1 → backend (default :8080)
```
Backend must be running (`python -m llm_manager` from repo root) for the dev proxy + data.

## Regenerate API types (after backend /api changes)
```bash
python -m llm_manager &   # backend exposing /openapi.json
npm run gen:api           # → src/api/types.ts
```

## Build (production)
```bash
npm run build            # → webui/dist/, served by FastAPI StaticFiles
```

## Themes
Three built-in palettes (深色克制 / 浅色通透 / 暖灰沉静) via semantic CSS tokens in
`src/index.css`; switched by `data-theme` on `<html>` (`src/lib/theme.tsx`). Choice
persists in localStorage.
