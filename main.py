
import os
import asyncio
import json
import logging
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini-live-backend")

app = FastAPI()

# --- Configuration ---
# Models
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

DEFAULT_CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": "You are a helpful and friendly AI assistant.",
    "tools": [types.Tool(google_search=types.GoogleSearch())],
}

# Initialize Gemini Client
# WARNING: Hardcoded API Key as per user request. 
client = genai.Client(api_key="AIzaSyAnXMJ0YP6VtlEgY8y0NX4hZfCvBNmKIL0")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for bidirectional streaming with Gemini Live API.
    
    Protocol:
    - Client -> Server: JSON
        {
            "data": "<base64_encoded_data>",
            "mime_type": "audio/pcm" | "image/jpeg"
        }
    """
    await websocket.accept()
    logger.info("Client connected")

    try:
        # Connect to Gemini Live API
        async with client.aio.live.connect(
            model=MODEL, 
            config=DEFAULT_CONFIG
        ) as session:
            logger.info("Connected to Gemini Live API")
            
            async def receive_from_client():
                """Receives audio/video from client and forwards to Gemini."""
                try:
                    while True:
                        message = await websocket.receive_text()
                        data = json.loads(message)
                        
                        # Handle textual input explicitly
                        if "text" in data:
                            await session.send(input=data["text"], end_of_turn=True)
                            continue

                        # Handle real-time media (Audio/Video/Screen)
                        # Expects: {"data": "base64...", "mime_type": "..."}
                        if "data" in data and "mime_type" in data:
                             await session.send(input=data)

                except WebSocketDisconnect:
                    logger.info("Client disconnected from WebSocket")
                except Exception as e:
                    logger.error(f"Error in receive_from_client: {e}")

            async def send_to_client():
                """Receives responses from Gemini and forwards to client."""
                try:
                    while True:
                        turn = session.receive()
                        async for response in turn:
                            if response.server_content and response.server_content.model_turn:
                                for part in response.server_content.model_turn.parts:
                                    # Handle Audio
                                    if part.inline_data and part.inline_data.data:
                                        # Convert bytes to base64 string for JSON transport
                                        import base64
                                        b64_data = base64.b64encode(part.inline_data.data).decode('utf-8')
                                        await websocket.send_json({
                                            "audio": b64_data
                                        })
                                    
                                    # Handle Text (if any)
                                    if part.text:
                                        await websocket.send_json({
                                            "text": part.text
                                        })
                            
                            if response.server_content and response.server_content.interrupted:
                                await websocket.send_json({"interrupted": True})

                except Exception as e:
                    logger.error(f"Error in send_to_client: {e}")

            # Run both tasks concurrently
            await asyncio.gather(receive_from_client(), send_to_client())

    except Exception as e:
        logger.error(f"Connection error: {e}")
        await websocket.close()
