# AI Video Transcription & Q&A

[![ci](https://github.com/nerminekabadou/AI-Video-Transcription/actions/workflows/ci.yml/badge.svg)](https://github.com/nerminekabadou/AI-Video-Transcription/actions/workflows/ci.yml)

Upload a video, get its transcript, then ask questions about what was said. A Next.js frontend talks to a FastAPI backend. The backend extracts the audio with FFmpeg, transcribes it with AssemblyAI and answers questions with an open LLM served by the Groq API.

## Features

- **Video upload**: click or drag and drop. The browser checks the file type, and the server checks both type and size.
- **Background processing**: the upload returns a job ID right away. Audio extraction and transcription run in the background, and the frontend polls `/status/{jobId}`.
- **Transcription**: FFmpeg converts the audio to 16 kHz mono WAV, then AssemblyAI transcribes it.
- **Question answering**: an LLM answers questions using only the video's transcript.
- **Robust backend**: uploads are streamed to disk in chunks with a size limit, file names are never taken from the client, blocking work runs off the event loop, temporary files are always cleaned up, and the API returns clear error codes (400 / 404 / 413 / 422 / 502 / 504).

## Architecture

```
Next.js (app/page.js)                 FastAPI (main.py)
──────────────────────               ─────────────────────────────────────────────
POST /upload (video)   ───────────▶  stream to temp file ─▶ background task:
GET  /status/{jobId}   ◀── poll ───     FFmpeg → WAV ─▶ AssemblyAI → transcript
POST /ask {transcript, question} ─▶  Groq chat completion (open LLM) ────▶ answer
```

## Tech stack

| Layer    | Technologies |
|----------|--------------|
| Frontend | Next.js 16, React 19, Tailwind CSS 4 |
| Backend  | Python, FastAPI, Pydantic, FFmpeg |
| AI       | AssemblyAI (speech-to-text), Groq API (GPT-OSS / Llama) |
| Quality  | pytest, ESLint, GitHub Actions |

## Getting started

### Prerequisites
- Node.js 20+
- Python 3.10+
- FFmpeg: `brew install ffmpeg` (macOS), `sudo apt-get install ffmpeg` (Linux), or download it from [ffmpeg.org](https://ffmpeg.org/download.html) (Windows)
- Free API keys from [AssemblyAI](https://www.assemblyai.com/app/account) and [Groq](https://console.groq.com/keys)

### Installation

```bash
git clone https://github.com/nerminekabadou/AI-Video-Transcription.git
cd AI-Video-Transcription

npm install                        # frontend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # backend

cp .env.example .env               # then add your API keys
```

### Configuration (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ASSEMBLYAI_API_KEY` | yes | AssemblyAI key for transcription |
| `GROQ_API_KEY` | yes | Groq key for question answering |
| `GROQ_MODEL` | no | Preferred model. If it isn't set or is no longer available, the backend picks the first model your Groq account can use from a built-in preference list (Groq retires models regularly) |
| `MAX_UPLOAD_MB` | no | Maximum upload size, default `200` |
| `FRONTEND_ORIGIN` | no | Allowed CORS origin, default `http://localhost:3000` |
| `FFMPEG_PATH` | no | Path to `ffmpeg` if it isn't on your `PATH` (forward slashes work on Windows, e.g. `C:/ffmpeg/bin/ffmpeg.exe`) |
| `NEXT_PUBLIC_API_URL` | no | Backend URL used by the frontend, default `http://localhost:8000` |

### Run

```bash
uvicorn main:app --reload          # backend on http://localhost:8000 (API docs at /docs)
npm run dev                        # frontend on http://localhost:3000
```

A short sample video for testing is in [`samples/`](samples/).

## API

| Method | Path | Body / Response |
|--------|------|-----------------|
| POST | `/upload` | multipart `file` (video) → `{"jobId": "…", "message": "Video upload started"}` |
| GET | `/status/{jobId}` | → `{"status": "processing" \| "completed" \| "failed", "transcript": "…", "error": null}` |
| POST | `/ask` | `{"transcript": "…", "question": "…"}` → `{"answer": "…"}` |

## Tests

```bash
pip install -r requirements-dev.txt
pytest                             # backend API tests
npm run lint && npm run build      # frontend checks
```

## Troubleshooting

- **FFmpeg errors on Windows** (e.g. `[WinError 87]`): make sure `ffmpeg.exe` is on your `PATH` or set `FFMPEG_PATH`. The `error` field returned by `/status/{jobId}` contains FFmpeg's own error output.
- **Large videos**: raise `MAX_UPLOAD_MB`. Audio extraction times out after 5 minutes.
- **Jobs disappear after a restart**: job status is kept in memory. A production version would store it in Redis or a database.

## Author
Nermine Kabadou ([@nerminekabadou](https://github.com/nerminekabadou))
