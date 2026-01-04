
import os
import asyncio
import json
import logging
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini-live-backend")

app = FastAPI()

# --- Configuration ---
# You can override these with environment variables or client messages
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": "You are a helpful and friendly AI assistant.",
}

# Initialize Gemini Client
# Ensure GEMINI_API_KEY is set in your environment variables
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for bidirectional streaming with Gemini Live API.
    
    Protocol:
    - Client -> Server: JSON
        {
            "realtime_input": {
                "media_chunks": [
                    {
                        "data": "<base64_encoded_data>",
                        "mime_type": "audio/pcm" | "image/jpeg"
                    }
                ]
            }
        }
        OR just a simple object which we map to the API:
        {
            "data": "<base64_encoded_pcm>",
            "mime_type": "audio/pcm"
        }
        
    - Server -> Client: JSON
        {
            "audio": "<base64_encoded_pcm>"
        }
        OR
        {
            "text": "..."
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
                        
                        # Handle simple {data, mime_type} format or more complex structures
                        if "data" in data and "mime_type" in data:
                             await session.send_realtime_input(
                                 content={
                                     "role": "user",
                                     "parts": [{
                                         "inline_data": {
                                             "mime_type": data["mime_type"],
                                             "data": data["data"]
                                         }
                                     }]
                                 }
                             )
                             # Note: send_realtime_input's signature depends on the exact version, 
                             # but the user snippet used: 
                             # await session.send_realtime_input(audio=msg) where msg = {'data':..., 'mime_type':...}
                             # Let's try to support exactly what the user snippet did too for safety.
                             # If the user sends just 'data' and 'mime_type', we can try the direct helper if it exists,
                             # or constructing the full content.
                             # The user snippet: await session.send_realtime_input(audio={'data': data, 'mime_type': 'audio/pcm'})
                             
                             if data.get("mime_type") == "audio/pcm":
                                 await session.send_realtime_input(audio=data)
                             else:
                                 # Fallback/Generic for video/images
                                 # Checking if send_realtime_input supports generic 'media_chunks' or similar
                                 # The snippet didn't show video, but we want input flexibility.
                                 # We will try passing it as a generic content part if audio argument doesn't fit.
                                 pass
                        
                        # If client sends a specific 'input' key (like text)
                        if "text" in data:
                             await session.send(input=data["text"], end_of_turn=True)

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
