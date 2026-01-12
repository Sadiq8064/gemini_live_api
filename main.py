import os
import asyncio
import json
import logging
from typing import Dict, Any
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini-live-backend")

app = FastAPI()

# --- MongoDB Configuration ---
MONGODB_URI = "mongodb+srv://wisdomkagyan_db_user:gqbCoXr99sKOcXEw@cluster0.itxqujm.mongodb.net/?appName=Cluster0"
DB_NAME = "gemini_live"
COLLECTION_NAME = "memory"

try:
    mongo_client = MongoClient(MONGODB_URI)
    db = mongo_client[DB_NAME]
    memory_collection = db[COLLECTION_NAME]
    logger.info("Connected to MongoDB successfully")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

# --- Configuration ---
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

DEFAULT_CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": "You are a helpful and friendly AI assistant.",
    "tools": [types.Tool(google_search=types.GoogleSearch())],
}

# Initialize Gemini Client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is required")
client = genai.Client(api_key=GEMINI_API_KEY)


# --- Memory Functions ---
async def save_memory(session_id: str, memory_text: str, context: str = ""):
    """Save a memory to MongoDB"""
    try:
        memory_doc = {
            "session_id": session_id,
            "memory_text": memory_text,
            "context": context,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = memory_collection.insert_one(memory_doc)
        logger.info(f"Memory saved with ID: {result.inserted_id}")
        
        # Return confirmation message
        return {
            "success": True,
            "message": f"I've remembered: '{memory_text}'",
            "memory_id": str(result.inserted_id)
        }
    except Exception as e:
        logger.error(f"Error saving memory: {e}")
        return {
            "success": False,
            "message": "Sorry, I couldn't save that memory"
        }


async def load_memories(session_id: str, limit: int = 10):
    """Load memories for a session from MongoDB"""
    try:
        memories = list(memory_collection.find(
            {"session_id": session_id}
        ).sort("created_at", -1).limit(limit))
        
        # Convert ObjectId to string for JSON serialization
        for memory in memories:
            memory["_id"] = str(memory["_id"])
            memory["created_at"] = memory["created_at"].isoformat()
            memory["updated_at"] = memory["updated_at"].isoformat()
        
        logger.info(f"Loaded {len(memories)} memories for session {session_id}")
        return memories
    except Exception as e:
        logger.error(f"Error loading memories: {e}")
        return []


async def process_memory_request(session_id: str, text: str, session):
    """Process memory-related requests from user"""
    memory_keywords = ["remember", "memorize", "save", "store", "keep in mind", "don't forget"]
    
    text_lower = text.lower()
    
    # Check if this is a memory request
    is_memory_request = any(keyword in text_lower for keyword in memory_keywords)
    
    if is_memory_request:
        # Extract memory text (remove the request part)
        memory_text = text
        for keyword in memory_keywords:
            if keyword in text_lower:
                # Try to extract just the thing to remember
                parts = text.split(keyword, 1)
                if len(parts) > 1:
                    memory_text = parts[1].strip()
                    if memory_text.lower().startswith("that"):
                        memory_text = memory_text[4:].strip()
                    if memory_text.lower().startswith("this"):
                        memory_text = memory_text[4:].strip()
                    if memory_text.lower().startswith("to"):
                        memory_text = memory_text[2:].strip()
                break
        
        # Save the memory
        result = await save_memory(session_id, memory_text, text)
        
        # Send confirmation to client
        await session.send(input=f"Memory saved successfully! {result['message']}", end_of_turn=True)
        
        return True, memory_text
    
    return False, None


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for bidirectional streaming with Gemini Live API.
    """
    await websocket.accept()
    logger.info("Client connected")
    
    # Generate a session ID (you might want to use a proper session management system)
    session_id = str(ObjectId())

    try:
        # Load existing memories for this session
        memories = await load_memories(session_id)
        
        # Add memories to system instruction if any exist
        config = DEFAULT_CONFIG.copy()
        if memories:
            memory_texts = [f"- {m['memory_text']}" for m in memories[:5]]  # Last 5 memories
            memory_context = "Here are things I should remember:\n" + "\n".join(memory_texts)
            config["system_instruction"] = f"{DEFAULT_CONFIG['system_instruction']}\n\n{memory_context}"
            logger.info(f"Loaded {len(memory_texts)} memories into context")
        
        # Connect to Gemini Live API with enhanced system instruction
        async with client.aio.live.connect(
            model=MODEL, 
            config=config
        ) as session:
            logger.info("Connected to Gemini Live API with memory context")
            
            # Send initial greeting with memory count
            if memories:
                memory_count = len(memories)
                await session.send(
                    input=f"Hello! I have {memory_count} memories loaded from our previous conversations. How can I help you today?",
                    end_of_turn=True
                )
            else:
                await session.send(
                    input="Hello! I'm ready to help. You can ask me to remember things by saying something like 'Remember that I like coffee'.",
                    end_of_turn=True
                )
            
            async def receive_from_client():
                """Receives audio/video from client and forwards to Gemini."""
                try:
                    while True:
                        message = await websocket.receive_text()
                        data = json.loads(message)
                        
                        # Handle textual input explicitly
                        if "text" in data:
                            user_text = data["text"]
                            
                            # Check if this is a memory request
                            is_memory, memory_text = await process_memory_request(
                                session_id, user_text, session
                            )
                            
                            if not is_memory:
                                # If not a memory request, send to Gemini as normal
                                await session.send(input=user_text, end_of_turn=True)
                            
                            continue

                        # Handle real-time media (Audio/Video/Screen)
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
