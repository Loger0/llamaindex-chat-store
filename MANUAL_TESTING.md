# Manual Testing Guide — VastbaseChatStore

## Prerequisites

- Vastbase G100 instance reachable at the configured host/port
- Python 3.9+ with `llama-index-storage-chat-store-vastbase` installed
- `pyvastbase >= 0.2.7`

## Environment Setup

```bash
export VASTBASE_HOST=172.16.105.107
export VASTBASE_PORT=15432
export VASTBASE_DATABASE=vastbase
export VASTBASE_USER=aidev
export VASTBASE_PASSWORD=Vbase_123456
```

## Quick Smoke Test

```python
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.storage.chat_store.vastbase import VastbaseChatStore

store = VastbaseChatStore.from_params(
    host="172.16.105.107",
    port=15432,
    database="vastbase",
    user="aidev",
    password="Vbase_123456",
    table_name="manual_test",
)

# 1. Set messages
store.set_messages("test_key", [
    ChatMessage(role=MessageRole.USER, content="Hello"),
    ChatMessage(role=MessageRole.ASSISTANT, content="Hi!"),
])
print("✅ set_messages OK")

# 2. Get messages
msgs = store.get_messages("test_key")
assert len(msgs) == 2
print(f"✅ get_messages OK — {len(msgs)} messages")

# 3. Add message
store.add_message("test_key", ChatMessage(role=MessageRole.USER, content="How are you?"))
msgs = store.get_messages("test_key")
assert len(msgs) == 3
print(f"✅ add_message OK — {len(msgs)} messages")

# 4. Delete last
deleted = store.delete_last_message("test_key")
assert deleted.content == "How are you?"
print(f"✅ delete_last_message OK — deleted: '{deleted.content}'")

# 5. Delete by index
deleted = store.delete_message("test_key", 0)
assert deleted.content == "Hello"
print(f"✅ delete_message OK — deleted: '{deleted.content}'")

# 6. Get keys
keys = store.get_keys()
assert "test_key" in keys
print(f"✅ get_keys OK — {keys}")

# 7. Delete all
store.delete_messages("test_key")
assert store.get_messages("test_key") == []
print("✅ delete_messages OK — all cleared")

print("\n🎉 All manual tests passed!")
```

## Async Smoke Test

```python
import asyncio
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.storage.chat_store.vastbase import VastbaseChatStore

async def main():
    store = VastbaseChatStore.from_params(
        host="172.16.105.107",
        port=15432,
        database="vastbase",
        user="aidev",
        password="Vbase_123456",
        table_name="manual_test_async",
    )

    await store.aset_messages("async_key", [
        ChatMessage(role=MessageRole.USER, content="Async hello"),
    ])
    msgs = await store.aget_messages("async_key")
    assert len(msgs) == 1
    print("✅ async set/get OK")

    await store.async_add_message("async_key",
        ChatMessage(role=MessageRole.ASSISTANT, content="Async reply"))
    msgs = await store.aget_messages("async_key")
    assert len(msgs) == 2
    print("✅ async add OK")

    await store.adelete_messages("async_key")
    keys = await store.aget_keys()
    assert "async_key" not in keys
    print("✅ async delete OK")

    print("\n🎉 All async manual tests passed!")

asyncio.run(main())
```

## Running Automated Tests

```bash
# Full test suite
python3 -m pytest tests/ -v

# Individual test files
python3 -m pytest tests/test_chat_store_sync.py -v      # 22 sync tests
python3 -m pytest tests/test_chat_store_async.py -v      # 17 async tests
python3 -m pytest tests/test_chat_store_init.py -v       # 11 init tests
python3 -m pytest tests/test_chat_store_integration.py -v # 7 integration tests
python3 -m pytest tests/test_compat.py -v                 # 11 compatibility tests
```
