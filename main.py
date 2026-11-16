from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import re
import uuid
import asyncio
from typing import Dict
import requests
import subprocess
import tempfile
import shutil
import shlex
import assemblyai as aai
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv()

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure AssemblyAI API key from environment variable
assemblyai_api_key = os.getenv("ASSEMBLYAI_API_KEY")
if not assemblyai_api_key:
    raise ValueError("ASSEMBLYAI_API_KEY environment variable is not set")
aai.settings.api_key = assemblyai_api_key

# Upload limits and temporary storage
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
UPLOAD_DIR = tempfile.gettempdir()

# Groq retires models regularly, so the model is resolved at runtime: GROQ_MODEL if set
# and available, otherwise the first available model from this preference list.
GROQ_PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
]
_NON_CHAT_MODELS = ("whisper", "guard", "tts", "playai", "orpheus", "compound")
_resolved_groq_model = None
MAX_TRANSCRIPT_CHARS = 4000


class AskRequest(BaseModel):
    transcript: str = Field(min_length=1)
    question: str = Field(min_length=1)

# Store job status in memory (use Redis or database in production)
job_status: Dict[str, dict] = {}

# Endpoint to upload video
@app.post("/upload")
async def upload_video(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not (file.content_type or "").startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")

    job_id = str(uuid.uuid4())

    # Never build paths from the client-supplied file name: keep only a safe extension.
    extension = os.path.splitext(file.filename or "")[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", extension):
        extension = ".video"
    video_file_path = os.path.join(UPLOAD_DIR, f"video_ai_{job_id}{extension}")

    # Stream the upload to disk in chunks instead of loading it into memory.
    size = 0
    with open(video_file_path, "wb") as buffer:
        while chunk := await file.read(CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                break
            buffer.write(chunk)
    if size > MAX_UPLOAD_BYTES:
        os.remove(video_file_path)
        raise HTTPException(status_code=413, detail=f"Video is larger than {MAX_UPLOAD_MB} MB")

    job_status[job_id] = {"status": "processing", "transcript": None, "error": None}
    background_tasks.add_task(process_video, job_id, video_file_path)

    return {"jobId": job_id, "message": "Video upload started"}

async def process_video(job_id: str, video_file_path: str):
    audio_file_path = None
    try:
        # Extract audio from video
        audio_file_path = os.path.splitext(video_file_path)[0] + '.wav'

        # Run the blocking ffmpeg call in a worker thread so the API stays responsive
        await asyncio.to_thread(extract_audio_from_video, video_file_path, audio_file_path)
        
        # Transcribe audio
        transcription = await asyncio.to_thread(transcribe_audio, audio_file_path)
        
        # Update job status
        job_status[job_id] = {
            "status": "completed",
            "transcript": transcription,
            "error": None
        }
        
    except Exception as e:
        job_status[job_id] = {
            "status": "failed",
            "transcript": None,
            "error": str(e)
        }
    
    finally:
        # Clean up temp files
        if os.path.exists(video_file_path):
            os.remove(video_file_path)
        if audio_file_path and os.path.exists(audio_file_path):
            os.remove(audio_file_path)

def extract_audio_from_video(video_path: str, audio_path: str):
    """
    Extract audio from video using ffmpeg
    Converts to WAV format with proper settings for AssemblyAI
    """
    try:
        # Resolve ffmpeg executable and handle possible extra args in FFMPEG_PATH
        ffmpeg_env = os.getenv("FFMPEG_PATH", "").strip()

        if ffmpeg_env:
            # On Windows users may paste a quoted path or include extra flags.
            # Use shlex.split with posix=False to preserve Windows-style parsing.
            try:
                ffmpeg_cmd = shlex.split(ffmpeg_env, posix=False)
            except Exception:
                # Fallback: use raw string as single command
                ffmpeg_cmd = [ffmpeg_env.strip('"')]

            # If the executable isn't an absolute path, try to locate it
            exe = ffmpeg_cmd[0]
            if not os.path.isabs(exe) and shutil.which(exe):
                ffmpeg_cmd[0] = shutil.which(exe)
        else:
            # Try to find ffmpeg on PATH
            ff = shutil.which('ffmpeg')
            if not ff:
                raise FileNotFoundError('FFmpeg not found in PATH and FFMPEG_PATH not set')
            ffmpeg_cmd = [ff]

        # Use absolute paths to avoid any relative path issues
        video_abs = os.path.abspath(video_path)
        audio_abs = os.path.abspath(audio_path)

        command = (
            ffmpeg_cmd
            + ['-i', video_abs, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', '-y', audio_abs]
        )

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=300  # 5 minute timeout for extraction
        )
        
        if not os.path.exists(audio_path):
            raise Exception("Audio extraction failed - output file not created")
            
    except subprocess.TimeoutExpired:
        raise Exception("Audio extraction timed out - video may be too long")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors='ignore') if e.stderr else ''
        stdout = e.stdout.decode(errors='ignore') if e.stdout else ''
        raise Exception(f"FFmpeg error (exit {e.returncode}): {stderr or stdout}")
    except FileNotFoundError as e:
        raise Exception(
            "FFmpeg not found. Install ffmpeg or set the `FFMPEG_PATH` environment variable to the ffmpeg executable."
        )
    except Exception as e:
        raise Exception(f"Audio extraction error: {str(e)}")

def transcribe_audio(audio_file_path: str):
    """
    Transcribe audio file using AssemblyAI
    Much simpler than Google Cloud!
    """
    try:
        transcriber = aai.Transcriber()
        
        # AssemblyAI automatically handles file upload and transcription
        transcript = transcriber.transcribe(audio_file_path)
        
        # Check for errors
        if transcript.status == aai.TranscriptStatus.error:
            raise Exception(f"Transcription failed: {transcript.error}")
        
        # Check if speech was detected
        if not transcript.text or len(transcript.text.strip()) == 0:
            raise Exception("No speech detected in video. Please ensure the video has clear audio.")
        
        return transcript.text
    
    except Exception as e:
        raise Exception(f"Transcription error: {str(e)}")

# Endpoint to check processing status
@app.get("/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in job_status:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return job_status[job_id]

def resolve_groq_model(api_key: str) -> str:
    """Pick a chat model that the Groq account can use right now (cached)."""
    global _resolved_groq_model
    if _resolved_groq_model:
        return _resolved_groq_model
    configured = os.getenv("GROQ_MODEL")
    try:
        response = requests.get("https://api.groq.com/openai/v1/models",
                                headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
        response.raise_for_status()
        available = [m["id"] for m in response.json()["data"] if m.get("active", True)]
    except (requests.exceptions.RequestException, KeyError, ValueError):
        return configured or GROQ_PREFERRED_MODELS[0]
    for model in [configured, *GROQ_PREFERRED_MODELS]:
        if model and model in available:
            _resolved_groq_model = model
            return model
    chat_models = [m for m in available if not any(word in m for word in _NON_CHAT_MODELS)]
    _resolved_groq_model = chat_models[0] if chat_models else (configured or GROQ_PREFERRED_MODELS[0])
    return _resolved_groq_model


# Endpoint to answer question
@app.post("/ask")
def ask_question(request: AskRequest):
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY environment variable is not set")

    # Limit transcript to avoid token limits
    transcript = request.transcript[:MAX_TRANSCRIPT_CHARS]
    payload = {
        "model": resolve_groq_model(groq_api_key),
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that answers questions about video content "
                           "based on transcripts. Provide clear, concise answers.",
            },
            {
                "role": "user",
                "content": f"Video Transcript:\n{transcript}\n\nQuestion: {request.question}\n\n"
                           "Please answer based only on the transcript above.",
            },
        ],
        "max_tokens": 500,
        "temperature": 0.7,
    }

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_api_key}"},
            json=payload,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Request timed out. Please try again.")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the Groq API: {e}")

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Groq API error ({response.status_code}): {response.text}")

    return {"answer": response.json()["choices"][0]["message"]["content"]}


# Live demo: serve the sample video and the static frontend build from the same origin.
if os.getenv("SAMPLES_DIR"):
    app.mount("/samples", StaticFiles(directory=os.getenv("SAMPLES_DIR")), name="samples")
if os.getenv("STATIC_DIR"):
    app.mount("/", StaticFiles(directory=os.getenv("STATIC_DIR"), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)