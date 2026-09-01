"""
Session-based memory service for multi-turn conversations
"""
from typing import Dict, List
from backend.app.models.chat_models import ChatMessage
import json
from datetime import datetime

class MemoryService:
    """Service to manage conversation memory per session"""
    
    def __init__(self):
        # In-memory storage for sessions (in production, use Redis or database)
        self.sessions: Dict[str, List[ChatMessage]] = {}
    
    def get_chat_history(self, session_id: str) -> List[ChatMessage]:
        """Get conversation history for a session"""
        return self.sessions.get(session_id, [])
    
    def get_session_history(self, session_id: str) -> List[ChatMessage]:
        """Get conversation history for a session (alias for get_chat_history)"""
        return self.get_chat_history(session_id)
    
    def add_message(self, session_id: str, message: ChatMessage) -> None:
        """Add a message to session history"""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        
        # Add timestamp if not provided
        if message.timestamp is None:
            message.timestamp = datetime.now()
        
        self.sessions[session_id].append(message)
        
        # Keep only last 20 messages to avoid context overflow
        if len(self.sessions[session_id]) > 20:
            self.sessions[session_id] = self.sessions[session_id][-20:]
    
    def clear_session(self, session_id: str) -> None:
        """Clear conversation history for a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    def format_history_for_llm(self, session_id: str) -> str:
        """Format conversation history for LLM context"""
        history = self.get_session_history(session_id)
        if not history:
            return ""
        
        formatted = "Previous conversation:\n"
        for msg in history:
            formatted += f"{msg.role}: {msg.content}\n"
        
        return formatted

# Global memory service instance
memory_service = MemoryService()
