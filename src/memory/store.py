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

    def __init__(
        self,
        db_url: Optional[str] = None,
        backend: str = "auto",
        require_postgres: bool = False,
    ):
        """
        Initialize the memory store.

        Args:
            db_url: PostgreSQL connection URL. If None, uses file-based fallback.
                   Format: postgresql://user:pass@host:port/dbname
        """
        self.db_url = db_url
        self.backend = (backend or "auto").lower()
        self.require_postgres = require_postgres
        self._conn = None
        self._fallback_reason = ""

        if self.backend not in {"auto", "postgresql", "file"}:
            raise ValueError(f"Unsupported memory backend: {backend}")

        postgres_required = self.require_postgres or self.backend == "postgresql"
        if self.backend == "file":
            self._use_file_fallback = True
            self._fallback_reason = "file backend requested"
        elif not PSYCOPG2_AVAILABLE:
            if postgres_required:
                raise RuntimeError("PostgreSQL backend requested but psycopg2 is not installed")
            self._use_file_fallback = True
            self._fallback_reason = "psycopg2 unavailable"
        elif not db_url:
            if postgres_required:
                raise RuntimeError("PostgreSQL backend requested but memory_db_url is not configured")
            self._use_file_fallback = True
            self._fallback_reason = "memory_db_url not configured"
        else:
            self._use_file_fallback = False

        if not self._use_file_fallback:
            self.backend = "postgresql"
            self._init_db()
        else:
            self.backend = "file"
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
            cur.execute("""
                ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS search_vector tsvector
                GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', coalesce(role, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(content, '')), 'B')
                ) STORED
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

            # Searchable knowledge table for durable memory/retrieval
            cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT,
                    category TEXT DEFAULT 'general',
                    session_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    metadata JSONB DEFAULT '{}'::jsonb,
                    search_vector tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('english', coalesce(key, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(source, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(content, '')), 'B')
                    ) STORED
                )
            """)

            # Create indexes
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_session_timestamp
                ON conversations(session_id, timestamp DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_role
                ON conversations(role)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversations_search
                ON conversations USING GIN(search_vector)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                ON sessions(updated_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_executions_session_timestamp
                ON tool_executions(session_id, timestamp DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_memories_category
                ON user_memories(category)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_entries_category_source
                ON knowledge_entries(category, source)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_entries_session
                ON knowledge_entries(session_id, updated_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_entries_search
                ON knowledge_entries USING GIN(search_vector)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_entries_metadata
                ON knowledge_entries USING GIN(metadata)
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
                cur.execute("""
                    UPDATE sessions
                    SET updated_at = NOW(),
                        model_main = COALESCE(%s, model_main),
                        model_local = COALESCE(%s, model_local)
                    WHERE session_id = %s
                """, (model_main, model_local, session_id))
                return dict(row)

            cur.execute("""
                INSERT INTO sessions (session_id, model_main, model_local)
                VALUES (%s, %s, %s)
                RETURNING *
            """, (session_id, model_main, model_local))
            row = cur.fetchone()
            return dict(row)

    def _touch_session(self, session_id: str):
        if self._use_file_fallback:
            return
        with self._get_cursor() as cur:
            cur.execute("""
                UPDATE sessions
                SET updated_at = NOW()
                WHERE session_id = %s
            """, (session_id,))

    def get_status(self) -> Dict[str, Any]:
        """Return backend/health details for the active memory store."""
        return {
            "backend": self.backend,
            "postgres_enabled": not self._use_file_fallback,
            "psycopg2_available": PSYCOPG2_AVAILABLE,
            "db_url_configured": bool(self.db_url),
            "fallback_reason": self._fallback_reason,
        }

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model: str = None,
        metadata: Dict = None,
        tokens_used: Optional[int] = None,
    ) -> int:
        """Add a message to conversation history."""
        metadata = dict(metadata or {})
        if tokens_used is not None:
            metadata["tokens_used"] = tokens_used

        if self._use_file_fallback:
            return self._file_add_message(session_id, role, content, model, metadata)

        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO conversations (session_id, role, content, model, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (session_id, role, content, model, Json(metadata or {})))
            message_id = cur.fetchone()['id']
        self._touch_session(session_id)
        return message_id

    def get_conversation(self, session_id: str, limit: int = 100) -> List[Dict]:
        """Get conversation history for a session."""
        if self._use_file_fallback:
            return self._file_get_conversation(session_id, limit)

        with self._get_cursor() as cur:
            cur.execute("""
                SELECT role, content, model, timestamp, metadata
                FROM (
                    SELECT role, content, model, timestamp, metadata
                    FROM conversations
                    WHERE session_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                ) recent
                ORDER BY timestamp ASC
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
            deleted = cur.rowcount > 0
        self._touch_session(session_id)
        return deleted

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
            execution_id = cur.fetchone()['id']
        self._touch_session(session_id)
        return execution_id

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

    def upsert_knowledge(
        self,
        key: str,
        content: str,
        source: Optional[str] = None,
        category: str = "general",
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Store durable searchable knowledge."""
        if self._use_file_fallback:
            return self._file_upsert_knowledge(key, content, source, category, session_id, metadata)

        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_entries (key, content, source, category, session_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                SET content = EXCLUDED.content,
                    source = EXCLUDED.source,
                    category = EXCLUDED.category,
                    session_id = EXCLUDED.session_id,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING id
            """, (key, content, source, category, session_id, Json(metadata or {})))
            knowledge_id = cur.fetchone()['id']
        if session_id:
            self._touch_session(session_id)
        return knowledge_id

    def search_knowledge(
        self,
        query: str,
        limit: int = 10,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search knowledge entries by full text in PostgreSQL or substring fallback."""
        if self._use_file_fallback:
            return self._file_search_knowledge(query, limit, category, session_id)

        filters = []
        params: List[Any] = []
        if category:
            filters.append("category = %s")
            params.append(category)
        if session_id:
            filters.append("session_id = %s")
            params.append(session_id)

        where_clause = ""
        if filters:
            where_clause = "WHERE " + " AND ".join(filters) + " AND "
        else:
            where_clause = "WHERE "

        sql = f"""
            SELECT key, content, source, category, session_id, metadata, updated_at,
                   ts_rank(search_vector, websearch_to_tsquery('english', %s)) AS rank
            FROM knowledge_entries
            {where_clause}search_vector @@ websearch_to_tsquery('english', %s)
            ORDER BY rank DESC, updated_at DESC
            LIMIT %s
        """
        params = [query, *params, query, limit]

        with self._get_cursor() as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def list_knowledge(
        self,
        limit: int = 10,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List recent knowledge rows, optionally filtered by category/session/metadata."""
        if self._use_file_fallback:
            return self._file_list_knowledge(limit, category, session_id, metadata)

        filters = []
        params: List[Any] = []
        if category:
            filters.append("category = %s")
            params.append(category)
        if session_id:
            filters.append("session_id = %s")
            params.append(session_id)
        if metadata:
            filters.append("metadata @> %s")
            params.append(Json(metadata))

        where_clause = ""
        if filters:
            where_clause = "WHERE " + " AND ".join(filters)

        sql = f"""
            SELECT key, content, source, category, session_id, metadata, updated_at
            FROM knowledge_entries
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT %s
        """
        params.append(limit)
        with self._get_cursor() as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def count_knowledge_entries(self, category: Optional[str] = None) -> int:
        """Count stored knowledge rows."""
        if self._use_file_fallback:
            return self._file_count_knowledge_entries(category)

        with self._get_cursor() as cur:
            if category:
                cur.execute("""
                    SELECT COUNT(*) AS count
                    FROM knowledge_entries
                    WHERE category = %s
                """, (category,))
            else:
                cur.execute("""
                    SELECT COUNT(*) AS count
                    FROM knowledge_entries
                """)
            return int(cur.fetchone()["count"])

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

    def _file_get_knowledge_path(self) -> Path:
        return self._data_dir / "knowledge.json"

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

    def _file_upsert_knowledge(
        self,
        key: str,
        content: str,
        source: Optional[str] = None,
        category: str = "general",
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        path = self._file_get_knowledge_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        data[key] = {
            "content": content,
            "source": source,
            "category": category,
            "session_id": session_id,
            "metadata": metadata or {},
            "updated_at": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(data, indent=2))
        return len(data)

    def _file_search_knowledge(
        self,
        query: str,
        limit: int = 10,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        path = self._file_get_knowledge_path()
        if not path.exists():
            return []

        q = query.lower()
        rows = []
        for key, item in json.loads(path.read_text()).items():
            if category and item.get("category") != category:
                continue
            if session_id and item.get("session_id") != session_id:
                continue
            haystack = " ".join([
                key,
                item.get("content", ""),
                item.get("source", "") or "",
            ]).lower()
            if q in haystack:
                rows.append({
                    "key": key,
                    **item,
                })
        rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
        return rows[:limit]

    def _file_list_knowledge(
        self,
        limit: int = 10,
        category: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        path = self._file_get_knowledge_path()
        if not path.exists():
            return []

        rows = []
        for key, item in json.loads(path.read_text()).items():
            if category and item.get("category") != category:
                continue
            if session_id and item.get("session_id") != session_id:
                continue
            item_metadata = item.get("metadata", {}) or {}
            if metadata and not all(item_metadata.get(name) == value for name, value in metadata.items()):
                continue
            rows.append({"key": key, **item})
        rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
        return rows[:limit]

    def _file_count_knowledge_entries(self, category: Optional[str] = None) -> int:
        path = self._file_get_knowledge_path()
        if not path.exists():
            return 0

        data = json.loads(path.read_text())
        if category is None:
            return len(data)
        return sum(1 for item in data.values() if item.get("category") == category)
