"""Vastbase chat store for LlamaIndex.

Drop-in replacement for PostgresChatStore using pyvastbase Collection API.
All database operations use pyvastbase Collection/AsyncCollection — no SQLAlchemy,
psycopg, asyncpg, or raw SQL.

Reference: llama-index-storage-chat-store-postgres v0.4.0
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, List, Optional
from urllib.parse import urlparse

from llama_index.core.bridge.pydantic import Field, PrivateAttr, ValidationError
from llama_index.core.llms import ChatMessage
from llama_index.core.storage.chat_store.base import BaseChatStore

logger = logging.getLogger(__name__)

# P3 #8 (TES-40): module-level set to hold cleanup task references,
# preventing GC from collecting fire-and-forget tasks in __del__.
# Module-level to avoid Pydantic intercepting it as a model field.
_VASTBASE_CLEANUP_TASKS: set = set()


class VastbaseChatStore(BaseChatStore):
    """Vastbase-backed chat store for LlamaIndex.

    Drop-in replacement for PostgresChatStore.
    Uses pyvastbase Collection API instead of SQLAlchemy + psycopg/asyncpg.

    Connection can be provided via:
        - from_params(host, port, database, user, password, ...)
        - from_uri("vastbase://user:pass@host:port/db")
        - Direct constructor with host/port/database/user/password fields

    Collection Schema:
        id (INT64, PK) | key (VARCHAR 512) | value (TEXT)

    The ``value`` column stores the entire ``List[ChatMessage]`` serialized
    as a JSON string. All array operations (append, pop, delete-by-index)
    are performed in the Python layer: SELECT → list operation → upsert.
    """

    # === Pydantic fields ===
    table_name: str = "chatstore"
    schema_name: str = "public"
    use_jsonb: bool = False  # Kept for API compat; no behavioral difference

    # Connection params
    host: str = "localhost"
    port: int = 15432
    database: str = "vastbase"
    user: str = ""
    password: str = Field(default="", repr=False)  # P0 #2: mask in repr/dump

    # === Private attributes ===
    _coll: Optional[object] = PrivateAttr(default=None)
    _async_coll: Optional[object] = PrivateAttr(default=None)
    _initialized: bool = PrivateAttr(default=False)
    _actual_table_name: str = PrivateAttr(default="")
    _id_counter: int = PrivateAttr(default=0)
    _async_lock: Optional[asyncio.Lock] = PrivateAttr(default=None)

    def __init__(self, **data):
        """Initialize VastbaseChatStore.

        Accepts all BaseChatStore + VastbaseChatStore Pydantic fields.
        Call _initialize() to set up connection and Collection.
        """
        # P1 #6: lowercase table_name in direct constructor too
        if "table_name" in data and data["table_name"]:
            data["table_name"] = data["table_name"].lower()

        super().__init__(**data)
        self._coll = None
        self._async_coll = None
        self._initialized = False
        self._actual_table_name = ""
        self._id_counter = 0
        # P2 #4 (TES-40): create lock eagerly to avoid TOCTOU race in
        # _ensure_async_initialized().  Python 3.10+ allows Lock() without
        # a running loop; on 3.9 the constructor calls get_event_loop()
        # which succeeds on the main thread (the common case).
        try:
            self._async_lock = asyncio.Lock()
        except RuntimeError:
            self._async_lock = None  # will be created lazily as fallback

        # P2 #7: warn if schema_name is non-default (not wired to any API)
        if self.schema_name and self.schema_name != "public":
            logger.warning(
                "schema_name='%s' is accepted but not used by pyvastbase "
                "Collection API. All collections are created in the default "
                "schema. This parameter is reserved for future use.",
                self.schema_name,
            )

    # ==================================================================
    # Factory Methods
    # ==================================================================

    @classmethod
    def from_params(
        cls,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        table_name: str = "chatstore",
        schema_name: str = "public",
        use_jsonb: bool = False,
    ) -> "VastbaseChatStore":
        """Create VastbaseChatStore from individual connection parameters.

        Args:
            host: Vastbase server host (default: localhost).
            port: Vastbase server port (default: 15432).
            database: Database name (default: vastbase).
            user: Database user.
            password: Database password.
            table_name: Name of the chat store collection.
            schema_name: Schema name (reserved for future use).
            use_jsonb: Kept for upstream API compatibility (no-op).

        Returns:
            Initialized VastbaseChatStore instance.
        """
        store = cls(
            host=host or "localhost",
            port=port or 15432,
            database=database or "vastbase",
            user=user or "",
            password=password or "",
            table_name=table_name.lower() if table_name else "chatstore",
            schema_name=schema_name,
            use_jsonb=use_jsonb,
        )
        store._initialize()
        return store

    @classmethod
    def from_uri(
        cls,
        uri: str,
        table_name: str = "chatstore",
        schema_name: str = "public",
        use_jsonb: bool = False,
    ) -> "VastbaseChatStore":
        """Create VastbaseChatStore from a vastbase:// URI.

        URI format: vastbase://user:pass@host:port/database

        Args:
            uri: Connection URI with vastbase:// scheme.
            table_name: Name of the chat store collection.
            schema_name: Schema name (reserved for future use).
            use_jsonb: Kept for upstream API compatibility (no-op).

        Returns:
            Initialized VastbaseChatStore instance.

        Raises:
            ValueError: If the URI scheme is not 'vastbase'.
        """
        parsed = urlparse(uri)

        if parsed.scheme != "vastbase":
            raise ValueError(
                f"Unsupported URI scheme: '{parsed.scheme}'. "
                "Expected 'vastbase://'."
            )

        host = parsed.hostname or "localhost"
        port = parsed.port or 15432
        database = parsed.path.strip("/") or "vastbase"
        user = parsed.username or ""
        password = parsed.password or ""

        store = cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            table_name=table_name.lower() if table_name else "chatstore",
            schema_name=schema_name,
            use_jsonb=use_jsonb,
        )
        store._initialize()
        return store

    # ==================================================================
    # Initialization
    # ==================================================================

    def _initialize(self) -> None:
        """Set up pyvastbase connection and Collection.

        1. Connect to Vastbase (sync, using global connection pool).
        2. Check for legacy table name (``data_{table_name}``).
        3. Define CollectionSchema: id (INT64 PK), key (VARCHAR 512),
           value (TEXT).
        4. Create or open Collection.
        """
        from pyvastbase import Collection, connect, has_collection
        from pyvastbase import CollectionSchema, DataType, FieldSchema

        alias = f"chatstore_{self.table_name}"

        # 1. Establish connection (both default and named alias)
        #    Utility functions like has_collection/drop_collection use the
        #    'default' connection, so we register with both.
        conn_kwargs = dict(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
        )
        try:
            connect(**conn_kwargs, using="default")
        except Exception as e:
            # P1 #1 (TES-40): only swallow "already exists" / "duplicate" errors.
            # Removed "exist" check — it mis-matched "does not exist" etc.
            err_msg = str(e).lower()
            if "already" in err_msg or "duplicate" in err_msg:
                logger.debug("Default connection already exists, reusing.")
            else:
                raise ConnectionError(
                    f"Failed to connect to Vastbase (default alias): {e}"
                ) from e
        try:
            connect(**conn_kwargs, using=alias)
        except Exception as e:
            err_msg = str(e).lower()
            if "already" in err_msg or "duplicate" in err_msg:
                logger.debug(
                    "Connection already exists for alias '%s', reusing.", alias
                )
            else:
                raise ConnectionError(
                    f"Failed to connect to Vastbase (alias '{alias}'): {e}"
                ) from e

        # 2. Legacy table detection
        legacy_name = f"data_{self.table_name}"
        if has_collection(legacy_name):
            self._actual_table_name = legacy_name
            logger.info("Using legacy collection name: %s", legacy_name)
        else:
            self._actual_table_name = self.table_name

        # 3. Define schema
        schema = CollectionSchema(
            name=self._actual_table_name,
            fields=[
                FieldSchema(
                    name="id",
                    dtype=DataType.INT64,
                    is_primary_key=True,
                ),
                FieldSchema(
                    name="key",
                    dtype=DataType.VARCHAR,
                    max_length=512,
                ),
                FieldSchema(
                    name="value",
                    dtype=DataType.TEXT,
                ),
            ],
        )

        # 4. Create or open Collection
        if has_collection(self._actual_table_name):
            self._coll = Collection(self._actual_table_name)
            logger.info(
                "Collection '%s' already exists, reusing.",
                self._actual_table_name,
            )
        else:
            self._coll = Collection(
                self._actual_table_name, schema=schema
            )
            self._coll.create()
            logger.info(
                "Collection '%s' created.", self._actual_table_name
            )

        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Ensure the store is initialized before use (lazy init)."""
        if not self._initialized:
            self._initialize()

    def _next_id(self) -> int:
        """Generate the next unique ID for a new row.

        Uses a timestamp-based counter with a random component:
        ``int(time.time() * 1_000_000) * 1000 + counter + random_offset``.

        P2 #12: Added random offset to prevent multi-instance ID collisions.
        P3 #7 (TES-40): Changed multiplier from 100 to 1000 to prevent
        counter + random_offset from overflowing into the next microsecond's
        ID space (counter is unbounded, random_offset is 0–99).
        """
        self._id_counter += 1
        random_offset = random.randint(0, 99)
        return int(time.time() * 1_000_000) * 1000 + self._id_counter + random_offset

    @staticmethod
    def _escape(value: str) -> str:
        """Escape special characters in string values for pyvastbase expr.

        P0 #1: Handle backslashes before single quotes to prevent
        expression injection via escaped quote sequences.
        """
        return value.replace("\\", "\\\\").replace("'", "''")

    # ==================================================================
    # Synchronous Methods — Core CRUD (Wave 1)
    # ==================================================================

    def set_messages(
        self, key: str, messages: List[ChatMessage]
    ) -> None:
        """Store messages for a key, overwriting any existing messages.

        Args:
            key: Unique identifier for the conversation.
            messages: List of ChatMessage objects to store.

        Raises:
            ValueError: If a message cannot be serialized to JSON.
        """
        self._ensure_initialized()

        # P1 #4 / P2 #10: Serialize messages safely
        value = self._serialize_messages(messages)

        # Check if key already exists
        existing = self._coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if existing:
            # Update existing row via upsert (on PK id)
            self._coll.upsert([
                {"id": existing[0]["id"], "key": key, "value": value}
            ])
        else:
            # Insert new row with generated id
            self._coll.insert([
                {"id": self._next_id(), "key": key, "value": value}
            ])

    def get_messages(self, key: str) -> List[ChatMessage]:
        """Retrieve messages for a key.

        Args:
            key: Unique identifier for the conversation.

        Returns:
            List of ChatMessage objects, or empty list if key not found.
        """
        self._ensure_initialized()

        results = self._coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return []

        try:
            parsed = json.loads(results[0]["value"])
            return [ChatMessage.model_validate(m) for m in parsed]
        # P1 #3 (TES-40): catch ValidationError for dict "poison pill" rows
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            logger.warning(
                "Failed to deserialize messages for key '%s'", key
            )
            return []

    def add_message(
        self, key: str, message: ChatMessage
    ) -> None:
        """Append a single message to the list for a key.

        If the key does not exist, a new entry is created.

        Args:
            key: Unique identifier for the conversation.
            message: ChatMessage to append.
        """
        self._ensure_initialized()

        existing = self._coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not existing:
            # P2 #2 (TES-40): use _serialize_messages uniformly
            self._coll.insert([
                {
                    "id": self._next_id(),
                    "key": key,
                    "value": self._serialize_messages([message]),
                }
            ])
        else:
            # Key exists — append to array in Python, then upsert
            messages = json.loads(existing[0]["value"])
            messages.append(message.model_dump(mode="json"))
            self._coll.upsert([
                {
                    "id": existing[0]["id"],
                    "key": key,
                    "value": self._serialize_messages(messages),
                }
            ])

    # ==================================================================
    # Synchronous Methods — Delete & Keys (Waves 2-3)
    # ==================================================================

    def delete_messages(
        self, key: str
    ) -> Optional[List[ChatMessage]]:
        """Delete all messages for a key.

        Args:
            key: Unique identifier for the conversation.

        Returns:
            List of deleted ChatMessage objects, or None if key not found.
        """
        self._ensure_initialized()

        results = self._coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return None

        # Deserialize before deleting
        try:
            parsed = json.loads(results[0]["value"])
            messages = [ChatMessage.model_validate(m) for m in parsed]
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            # P2 #8: corrupted data — still delete the row but return None
            logger.warning(
                "Corrupted data for key '%s', deleting row and returning None",
                key,
            )
            self._coll.delete(expr=f"key = '{self._escape(key)}'")
            return None

        # Delete the row
        self._coll.delete(expr=f"key = '{self._escape(key)}'")

        return messages

    def delete_message(
        self, key: str, idx: int
    ) -> Optional[ChatMessage]:
        """Delete a single message at the given index for a key.

        Args:
            key: Unique identifier for the conversation.
            idx: Index of the message to delete (0-based).

        Returns:
            The deleted ChatMessage, or None if key/index not found.
        """
        self._ensure_initialized()

        results = self._coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return None

        try:
            value = results[0].get("value", "[]")
            if not value or value == "[]":
                return None
            messages = json.loads(value)
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return None

        if idx < 0 or idx >= len(messages):
            return None

        removed = messages.pop(idx)

        self._coll.upsert([
            {
                "id": results[0]["id"],
                "key": key,
                "value": json.dumps(messages),
            }
        ])

        return ChatMessage.model_validate(removed)

    def delete_last_message(
        self, key: str
    ) -> Optional[ChatMessage]:
        """Delete the last message for a key.

        Args:
            key: Unique identifier for the conversation.

        Returns:
            The deleted ChatMessage, or None if key not found or list empty.
        """
        self._ensure_initialized()

        results = self._coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return None

        try:
            value = results[0].get("value", "[]")
            if not value or value == "[]":
                return None
            messages = json.loads(value)
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return None

        if len(messages) == 0:
            return None

        removed = messages.pop()

        self._coll.upsert([
            {
                "id": results[0]["id"],
                "key": key,
                "value": json.dumps(messages),
            }
        ])

        return ChatMessage.model_validate(removed)

    def get_keys(self) -> List[str]:
        """Retrieve all keys stored in the chat store.

        Returns:
            List of key strings.
        """
        self._ensure_initialized()

        # P2 #11: explicit high limit to avoid truncation on large stores
        results = self._coll.query(
            expr="1=1", output_fields=["key"], limit=10000
        )

        # P3 #6 (TES-40): warn if results hit the limit (possible truncation)
        if len(results) == 10000:
            logger.warning(
                "get_keys() returned 10000 keys — results may be truncated. "
                "Consider increasing the limit or filtering server-side."
            )

        return [r["key"] for r in results]

    # ==================================================================
    # Asynchronous Methods (Wave 4 — native pyvastbase AsyncCollection)
    # ==================================================================

    async def _ensure_async_initialized(self) -> None:
        """Ensure the async Collection is available (lazy init).

        P2 #4 (TES-40): Lock is now created in __init__ to prevent TOCTOU.
        Fallback lazy creation kept for Python 3.9 edge case where Lock()
        may fail outside an event loop on non-main threads.
        P2 #15: Wraps connection in try/except for robust error handling.
        """
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()

        async with self._async_lock:
            if self._async_coll is None:
                from pyvastbase import AsyncCollection, AsyncConnections

                alias = f"chatstore_{self._actual_table_name or self.table_name}"
                try:
                    await AsyncConnections.connect(
                        alias,
                        host=self.host,
                        port=self.port,
                        database=self.database,
                        user=self.user,
                        password=self.password,
                    )
                except Exception as e:
                    err_msg = str(e).lower()
                    # P1 #1 (TES-40): removed "exist" check
                    if not ("already" in err_msg or "duplicate" in err_msg):
                        raise ConnectionError(
                            f"Failed to create async connection "
                            f"(alias '{alias}'): {e}"
                        ) from e

                self._async_coll = AsyncCollection(
                    self._actual_table_name or self.table_name,
                    using=alias,
                )

    def _serialize_messages(
        self, messages: List[Any]
    ) -> str:
        """Serialize a list of messages to a JSON string.

        Handles ChatMessage, dict, and other types with error reporting.

        P1 #4: Prevents double JSON serialization for dict messages.
        P1 #3 (TES-40): Validates dict messages against ChatMessage schema.
        P2 #10: Raises ValueError for unserializable objects.
        """
        serialized = []
        for i, m in enumerate(messages):
            try:
                if isinstance(m, ChatMessage):
                    serialized.append(m.model_dump(mode="json"))
                elif isinstance(m, dict):
                    # P1 #3 (TES-40): validate dict against ChatMessage schema
                    # to catch "poison pill" dicts at write time
                    ChatMessage.model_validate(m)
                    serialized.append(m)
                else:
                    logger.warning(
                        "Message at index %d is not a ChatMessage or dict "
                        "(type=%s), attempting model_dump.",
                        i, type(m).__name__,
                    )
                    serialized.append(m.model_dump(mode="json"))
            except (AttributeError, TypeError, ValueError, ValidationError) as e:
                raise ValueError(
                    f"Message at index {i} cannot be serialized: {e}"
                ) from e
        return json.dumps(serialized)

    async def aset_messages(
        self, key: str, messages: List[ChatMessage]
    ) -> None:
        """Async version of set_messages."""
        await self._ensure_async_initialized()

        value = self._serialize_messages(messages)

        existing = await self._async_coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if existing:
            await self._async_coll.upsert([
                {"id": existing[0]["id"], "key": key, "value": value}
            ])
        else:
            await self._async_coll.insert([
                {"id": self._next_id(), "key": key, "value": value}
            ])

    async def aget_messages(
        self, key: str
    ) -> List[ChatMessage]:
        """Async version of get_messages."""
        await self._ensure_async_initialized()

        results = await self._async_coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return []

        try:
            parsed = json.loads(results[0]["value"])
            return [ChatMessage.model_validate(m) for m in parsed]
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            logger.warning(
                "Async: failed to deserialize messages for key '%s'", key
            )
            return []

    async def async_add_message(
        self, key: str, message: ChatMessage
    ) -> None:
        """Async version of add_message."""
        await self._ensure_async_initialized()

        existing = await self._async_coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not existing:
            # P2 #2 (TES-40): use _serialize_messages uniformly
            await self._async_coll.insert([
                {
                    "id": self._next_id(),
                    "key": key,
                    "value": self._serialize_messages([message]),
                }
            ])
        else:
            messages = json.loads(existing[0]["value"])
            messages.append(message.model_dump(mode="json"))
            await self._async_coll.upsert([
                {
                    "id": existing[0]["id"],
                    "key": key,
                    "value": self._serialize_messages(messages),
                }
            ])

    async def adelete_messages(
        self, key: str
    ) -> Optional[List[ChatMessage]]:
        """Async version of delete_messages."""
        await self._ensure_async_initialized()

        results = await self._async_coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return None

        try:
            parsed = json.loads(results[0]["value"])
            messages = [ChatMessage.model_validate(m) for m in parsed]
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            # P2 #8: corrupted data — still delete but return None
            logger.warning(
                "Async: corrupted data for key '%s', deleting row", key
            )
            await self._async_coll.delete(
                expr=f"key = '{self._escape(key)}'"
            )
            return None

        await self._async_coll.delete(
            expr=f"key = '{self._escape(key)}'"
        )

        return messages

    async def adelete_message(
        self, key: str, idx: int
    ) -> Optional[ChatMessage]:
        """Async version of delete_message."""
        await self._ensure_async_initialized()

        results = await self._async_coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return None

        try:
            value = results[0].get("value", "[]")
            if not value or value == "[]":
                return None
            messages = json.loads(value)
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return None

        if idx < 0 or idx >= len(messages):
            return None

        removed = messages.pop(idx)

        await self._async_coll.upsert([
            {
                "id": results[0]["id"],
                "key": key,
                "value": json.dumps(messages),
            }
        ])

        return ChatMessage.model_validate(removed)

    async def adelete_last_message(
        self, key: str
    ) -> Optional[ChatMessage]:
        """Async version of delete_last_message."""
        await self._ensure_async_initialized()

        results = await self._async_coll.query(
            expr=f"key = '{self._escape(key)}'", limit=1
        )

        if not results:
            return None

        try:
            value = results[0].get("value", "[]")
            if not value or value == "[]":
                return None
            messages = json.loads(value)
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return None

        if len(messages) == 0:
            return None

        removed = messages.pop()

        await self._async_coll.upsert([
            {
                "id": results[0]["id"],
                "key": key,
                "value": json.dumps(messages),
            }
        ])

        return ChatMessage.model_validate(removed)

    async def aget_keys(self) -> List[str]:
        """Async version of get_keys."""
        await self._ensure_async_initialized()

        # P2 #11: explicit high limit to avoid truncation on large stores
        results = await self._async_coll.query(
            expr="1=1", output_fields=["key"], limit=10000
        )

        # P3 #6 (TES-40): warn if results hit the limit
        if len(results) == 10000:
            logger.warning(
                "aget_keys() returned 10000 keys — results may be truncated."
            )

        return [r["key"] for r in results]

    def __del__(self):
        """Best-effort cleanup of async connections on garbage collection.

        P2 #5 (TES-40): Use get_running_loop() instead of the deprecated
        asyncio API. Only schedule cleanup if a loop is actually running.
        P3 #8 (TES-40): Store task reference to prevent GC.
        """
        if self._async_coll is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._close_async())
                # P3 #8: prevent task from being garbage collected
                _VASTBASE_CLEANUP_TASKS.add(task)
                task.add_done_callback(_VASTBASE_CLEANUP_TASKS.discard)
            except RuntimeError:
                pass  # No running loop — connection will be cleaned up on exit

    async def _close_async(self):
        """Close the async collection connection."""
        try:
            from pyvastbase import AsyncConnections

            alias = f"chatstore_{self._actual_table_name or self.table_name}"
            await AsyncConnections.close(alias)
        except Exception:
            pass  # Best-effort cleanup
