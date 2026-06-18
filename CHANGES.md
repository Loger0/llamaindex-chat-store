# CHANGES — LlamaIndex ChatStore Vastbase Adapter

## TES-33: Wave 3 — 异步方法 + 集成测试 + 兼容性 + 文档

### Added
- **7 async methods** in `base.py`: `aset_messages`, `aget_messages`, `async_add_message`, `adelete_messages`, `adelete_message`, `adelete_last_message`, `aget_keys`
- **`_ensure_async_initialized()`**: Lazy async connection setup using `pyvastbase.AsyncConnections` + `AsyncCollection`
- **`test_chat_store_async.py`**: 17 async test cases covering all 7 async methods + edge cases + multimodal messages
- **`test_chat_store_integration.py`**: 7 end-to-end integration tests (CRUD lifecycle, multi-key, persistence, metadata, roles, idempotency, overwrite)
- **`test_compat.py`**: 11 compatibility tests verifying `BaseChatStore` inheritance, method existence, method signatures, and ChatMessage serialization
- **`from_uri()`**: Factory method accepting `vastbase://` URI scheme, rejecting `postgresql://`
- **README.md**: Complete usage examples (sync + async), API reference table, design notes
- **MANUAL_TESTING.md**: Manual smoke test guide for both sync and async operations

### Architecture Decisions
- **Native async**: All async methods use `pyvastbase.AsyncCollection` + `AsyncConnections` (not `asyncio.to_thread`)
- **Naming convention**: `a` prefix for async methods, except `async_add_message` (matches upstream inconsistency per D-01)
- **Error semantics**: Silent `None`/`[]` returns matching `PostgresChatStore` behavior (D-03)
- **URI scheme**: `vastbase://` only; `postgresql://` raises `ValueError` (D-06)

## TES-32: Wave 2 — 删除操作

### Added
- `delete_messages(key)`: Delete all messages for a key
- `delete_message(key, idx)`: Delete specific message by 0-based index with bounds checking
- `delete_last_message(key)`: Delete the last message
- `get_keys()`: List all stored conversation keys
- 22 sync test cases in `test_chat_store_sync.py`

## TES-31: Wave 1 — 核心 CRUD

### Added
- `set_messages(key, messages)`: Store messages with overwrite semantics
- `get_messages(key)`: Retrieve messages (returns `[]` for missing keys)
- `add_message(key, message)`: Append message to existing or new key
- 11 init tests in `test_chat_store_init.py`

## TES-30: Wave 0 — 基础设施

### Added
- Package scaffolding: `pyproject.toml`, `__init__.py`, namespace package structure
- `VastbaseChatStore` class inheriting `BaseChatStore`
- `from_params()` factory method
- Collection schema: `{id (INT64 PK), key (VARCHAR 512), value (TEXT)}`
- `_initialize()`: Lazy connection + auto-collection creation
- `_escape()`: String escaping for pyvastbase expr syntax
- `_next_id()`: Microsecond timestamp + counter ID generation
