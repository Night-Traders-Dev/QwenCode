"""
Memory system for QwenCode using PostgreSQL.

Provides persistent storage for:
- Conversation history (main LLM and local LLM)
- User preferences and context
- Tool execution results
- Session metadata
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None


class MemoryStore:
    """PostgreSQL-backed memory store for conversation history and context."""

    def __init__(self, db_url: Optional[str] = None):
        """
        Initialize the memory store.

        Args:
            db_url: PostgreSQL connection URL. If None, uses file-based fallback.
                   Format: postgresql://user:pass@host:port/dbname
        """
        self.db_url = db_url
        self._conn = None
        self._use_file_fallback = not PSYCOPG2_AVAILABLE or not db_url

        if not self._use_file_fallback:
            self._init_db()
        else:
            self._data_dir = Path.home() / ".qwencode" / "memory"
            self._data_dir.mkdir(parents=True, exist_ok=True)

    def _init_db(self):
        """Initialize database tables."""
        with self._get_cursor() as cur:
            # Conversations table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model TEXT,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Sessions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    model_main TEXT,
                    model_local TEXT,
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Tool executions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tool_executions (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments JSONB NOT NULL,
                    result TEXT,
                    success BOOLEAN DEFAULT true,
                    timestamp TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # User memories/preferences table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    value JSONB NOT NULL,
                    category TEXT DEFAULT 'general',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Create indexes
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_session
                ON conversations(session_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_role
                ON conversations(role)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_executions_session
                ON tool_executions(session_id)
            """)

    @contextmanager
    def _get_cursor(self):
        """Get a database cursor context manager."""
        if self._use_file_fallback:
            raise RuntimeError("Database not available, using file fallback")

        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                self.db_url,
                cursor_factory=RealDictCursor
            )

        try:
            cur = self._conn.cursor()
            yield cur
            self._conn.commit()
        except Exception as e:
            if self._conn:
                self._conn.rollback()
            raise e
        finally:
            if cur:
                cur.close()

    def get_or_create_session(self, session_id: str,
                               model_main: str = None,
                               model_local: str = None) -> Dict[str, Any]:
        """Get existing session or create new one."""
        if self._use_file_fallback:
            return self._file_get_or_create_session(session_id, model_main, model_local)

        with self._get_cursor() as cur:
            cur.execute("""
                SELECT * FROM sessions WHERE session_id = %s
            """, (session_id,))
            row = cur.fetchone()

            if row:
                return dict(row)

            cur.execute("""
                INSERT INTO sessions (session_id, model_main, model_local)
                VALUES (%s, %s, %s)
                RETURNING *
            """, (session_id, model_main, model_local))
            row = cur.fetchone()
            return dict(row)

    def add_message(self, session_id: str, role: str, content: str,
                    model: str = None, metadata: Dict = None) -> int:
        """Add a message to conversation history."""
        if self._use_file_fallback:
            return self._file_add_message(session_id, role, content, model, metadata)

        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO conversations (session_id, role, content, model, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (session_id, role, content, model, Json(metadata or {})))
            return cur.fetchone()['id']

    def get_conversation(self, session_id: str, limit: int = 100) -> List[Dict]:
        """Get conversation history for a session."""
        if self._use_file_fallback:
            return self._file_get_conversation(session_id, limit)

        with self._get_cursor() as cur:
            cur.execute("""
                SELECT role, content, model, timestamp, metadata
                FROM conversations
                WHERE session_id = %s
                ORDER BY timestamp ASC
                LIMIT %s
            """, (session_id, limit))
            return [dict(row) for row in cur.fetchall()]

    def clear_conversation(self, session_id: str) -> bool:
        """Clear conversation history for a session."""
        if self._use_file_fallback:
            return self._file_clear_conversation(session_id)

        with self._get_cursor() as cur:
            cur.execute("""
                DELETE FROM conversations WHERE session_id = %s
            """, (session_id,))
            return cur.rowcount > 0

    def log_tool_execution(self, session_id: str, tool_name: str,
                           arguments: Dict, result: str, success: bool = True) -> int:
        """Log a tool execution."""
        if self._use_file_fallback:
            return self._file_log_tool_execution(session_id, tool_name, arguments, result, success)

        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO tool_executions (session_id, tool_name, arguments, result, success)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (session_id, tool_name, Json(arguments), result, success))
            return cur.fetchone()['id']

    def get_tool_history(self, session_id: str, limit: int = 50) -> List[Dict]:
        """Get tool execution history."""
        if self._use_file_fallback:
            return self._file_get_tool_history(session_id, limit)

        with self._get_cursor() as cur:
            cur.execute("""
                SELECT tool_name, arguments, result, success, timestamp
                FROM tool_executions
                WHERE session_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (session_id, limit))
            return [dict(row) for row in cur.fetchall()]

    def set_memory(self, key: str, value: Any, category: str = 'general') -> bool:
        """Store a user memory/preference."""
        if self._use_file_fallback:
            return self._file_set_memory(key, value, category)

        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO user_memories (key, value, category)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
                RETURNING id
            """, (key, Json(value), category))
            return cur.fetchone() is not None

    def get_memory(self, key: str) -> Optional[Any]:
        """Retrieve a user memory/preference."""
        if self._use_file_fallback:
            return self._file_get_memory(key)

        with self._get_cursor() as cur:
            cur.execute("""
                SELECT value FROM user_memories WHERE key = %s
            """, (key,))
            row = cur.fetchone()
            return row['value'] if row else None

    def get_all_memories(self, category: str = None) -> Dict[str, Any]:
        """Get all user memories, optionally filtered by category."""
        if self._use_file_fallback:
            return self._file_get_all_memories(category)

        with self._get_cursor() as cur:
            if category:
                cur.execute("""
                    SELECT key, value FROM user_memories WHERE category = %s
                """, (category,))
            else:
                cur.execute("""
                    SELECT key, value FROM user_memories
                """)
            return {row['key']: row['value'] for row in cur.fetchall()}

    def close(self):
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    # ── File-based fallback methods ───────────────────────────────────────────

    def _file_get_session_path(self, session_id: str) -> Path:
        """Get path to session file."""
        session_hash = hashlib.md5(session_id.encode()).hexdigest()[:16]
        return self._data_dir / f"session_{session_hash}.json"

    def _file_get_or_create_session(self, session_id: str,
                                     model_main: str = None,
                                     model_local: str = None) -> Dict:
        path = self._file_get_session_path(session_id)
        if path.exists():
            data = json.loads(path.read_text())
            data['updated_at'] = datetime.now().isoformat()
            path.write_text(json.dumps(data, indent=2))
            return data

        data = {
            'session_id': session_id,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'model_main': model_main,
            'model_local': model_local,
            'metadata': {}
        }
        path.write_text(json.dumps(data, indent=2))
        return data

    def _file_add_message(self, session_id: str, role: str, content: str,
                          model: str = None, metadata: Dict = None) -> int:
        path = self._file_get_session_path(session_id)
        data = json.loads(path.read_text()) if path.exists() else {'messages': []}

        if 'messages' not in data:
            data['messages'] = []

        msg = {
            'role': role,
            'content': content,
            'model': model,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {}
        }
        data['messages'].append(msg)
        data['updated_at'] = datetime.now().isoformat()
        path.write_text(json.dumps(data, indent=2))
        return len(data['messages'])

    def _file_get_conversation(self, session_id: str, limit: int = 100) -> List[Dict]:
        path = self._file_get_session_path(session_id)
        if not path.exists():
            return []

        data = json.loads(path.read_text())
        messages = data.get('messages', [])[-limit:]
        return messages

    def _file_clear_conversation(self, session_id: str) -> bool:
        path = self._file_get_session_path(session_id)
        if not path.exists():
            return False

        data = json.loads(path.read_text())
        data['messages'] = []
        data['updated_at'] = datetime.now().isoformat()
        path.write_text(json.dumps(data, indent=2))
        return True

    def _file_log_tool_execution(self, session_id: str, tool_name: str,
                                  arguments: Dict, result: str, success: bool) -> int:
        path = self._file_get_session_path(session_id)
        data = json.loads(path.read_text()) if path.exists() else {'tool_executions': []}

        if 'tool_executions' not in data:
            data['tool_executions'] = []

        exec_record = {
            'tool_name': tool_name,
            'arguments': arguments,
            'result': result,
            'success': success,
            'timestamp': datetime.now().isoformat()
        }
        data['tool_executions'].append(exec_record)
        path.write_text(json.dumps(data, indent=2))
        return len(data['tool_executions'])

    def _file_get_tool_history(self, session_id: str, limit: int = 50) -> List[Dict]:
        path = self._file_get_session_path(session_id)
        if not path.exists():
            return []

        data = json.loads(path.read_text())
        executions = data.get('tool_executions', [])[-limit:]
        return list(reversed(executions))

    def _file_set_memory(self, key: str, value: Any, category: str = 'general') -> bool:
        mem_path = self._data_dir / "memories.json"
        data = json.loads(mem_path.read_text()) if mem_path.exists() else {}

        if category not in data:
            data[category] = {}

        data[category][key] = {
            'value': value,
            'updated_at': datetime.now().isoformat()
        }
        mem_path.write_text(json.dumps(data, indent=2))
        return True

    def _file_get_memory(self, key: str) -> Optional[Any]:
        mem_path = self._data_dir / "memories.json"
        if not mem_path.exists():
            return None

        data = json.loads(mem_path.read_text())
        for category in data.values():
            if key in category:
                return category[key].get('value')
        return None

    def _file_get_all_memories(self, category: str = None) -> Dict[str, Any]:
        mem_path = self._data_dir / "memories.json"
        if not mem_path.exists():
            return {}

        data = json.loads(mem_path.read_text())
        if category:
            return {k: v['value'] for k, v in data.get(category, {}).items()}

        result = {}
        for cat_data in data.values():
            for k, v in cat_data.items():
                result[k] = v['value']
        return result
