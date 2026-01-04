# Gemini Live API Backend (FastAPI)

This is a FastAPI backend service that acts as a WebSocket proxy for the Google Gemini Live API. It enables client applications (web, mobile) to stream audio/video to the backend, which forwards it to Gemini and returns the model's audio response.

## Prerequisites

- Python 3.11+
- Google Cloud Project with Gemini API enabled
- `GEMINI_API_KEY` environment variable set

## Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set your API key:
   ```bash
   export GEMINI_API_KEY="your_api_key_here"
   ```

## Running the Server

Run the FastAPI application using uvicorn:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```
Or using Docker:
```bash
docker build -t gemini-live-backend .
docker run -e GEMINI_API_KEY=$GEMINI_API_KEY -p 8000:8000 gemini-live-backend
```

## WebSocket Endpoint

**URL:** `ws://localhost:8000/ws`

### Protocol

**Client -> Server:**
Send JSON messages with audio data (base64 encoded PCM 16kHz mono).
```json
{
  "data": "<base64_string>",
  "mime_type": "audio/pcm"
}
```

**Server -> Client:**
Receives JSON messages with audio data (base64 encoded PCM 24kHz).
```json
{
  "audio": "<base64_string>"
}
```
