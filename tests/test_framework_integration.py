"""Framework-level integration tests — validates VastbaseChatStore through
LlamaIndex's high-level ChatMemoryBuffer API and real conversation workflows.

Layer 2: Framework Integration Acceptance (流程 C, Phase C2)
"""

import asyncio
import pytest

from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.storage.chat_store.vastbase import VastbaseChatStore

from conftest import VASTBASE_CONFIG, _make_table_name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fw_store():
    """Create a VastbaseChatStore for framework integration tests."""
    store = VastbaseChatStore.from_params(
        host=VASTBASE_CONFIG["host"],
        port=VASTBASE_CONFIG["port"],
        database=VASTBASE_CONFIG["database"],
        user=VASTBASE_CONFIG["user"],
        password=VASTBASE_CONFIG["password"],
        table_name=_make_table_name("fw_integration"),
    )
    yield store
    # Cleanup
    try:
        for key in store.get_keys():
            store.delete_messages(key)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scenario 1: ChatMemoryBuffer integration (LlamaIndex high-level API)
# ---------------------------------------------------------------------------

def test_chat_memory_buffer_with_vastbase_chat_store(fw_store: VastbaseChatStore):
    """ChatMemoryBuffer should use VastbaseChatStore as its backing store.

    This is the primary framework integration test — validates that
    VastbaseChatStore works as a drop-in replacement within LlamaIndex's
    standard memory infrastructure.
    """
    memory = ChatMemoryBuffer.from_defaults(
        chat_store=fw_store,
        chat_store_key="test_memory_buffer",
        token_limit=3000,
    )

    # Put messages through the memory buffer
    memory.put(ChatMessage(content="Hello, how are you?", role="user"))
    memory.put(ChatMessage(content="I'm doing well, thanks!", role="assistant"))
    memory.put(ChatMessage(content="What can you help with?", role="user"))

    # Retrieve via memory
    all_messages = memory.get_all()
    assert len(all_messages) == 3
    assert all_messages[0].content == "Hello, how are you?"
    assert all_messages[2].content == "What can you help with?"

    # Verify messages are persisted in the backing store
    stored = fw_store.get_messages("test_memory_buffer")
    assert len(stored) == 3

    # Reset via memory
    memory.reset()
    assert memory.get_all() == []
    assert fw_store.get_messages("test_memory_buffer") == []


def test_chat_memory_buffer_multiple_sessions(fw_store: VastbaseChatStore):
    """Multiple ChatMemoryBuffer instances sharing a store should maintain
    independent conversation histories."""
    memory_a = ChatMemoryBuffer.from_defaults(
        chat_store=fw_store,
        chat_store_key="session_a",
        token_limit=3000,
    )
    memory_b = ChatMemoryBuffer.from_defaults(
        chat_store=fw_store,
        chat_store_key="session_b",
        token_limit=3000,
    )

    memory_a.put(ChatMessage(content="A's conversation", role="user"))
    memory_b.put(ChatMessage(content="B's conversation", role="user"))

    a_messages = memory_a.get_all()
    b_messages = memory_b.get_all()

    assert len(a_messages) == 1
    assert a_messages[0].content == "A's conversation"
    assert len(b_messages) == 1
    assert b_messages[0].content == "B's conversation"


# ---------------------------------------------------------------------------
# Scenario 2: Multi-turn conversation simulation
# ---------------------------------------------------------------------------

def test_multi_turn_conversation_workflow(fw_store: VastbaseChatStore):
    """Simulate a realistic multi-turn user-assistant conversation with
    message management operations (delete, edit, etc.)."""
    key = "multi_turn_conv"

    # Turn 1
    fw_store.add_message(key, ChatMessage(content="Hi", role="user"))
    fw_store.add_message(key, ChatMessage(content="Hello! How can I help?", role="assistant"))

    # Turn 2
    fw_store.add_message(key, ChatMessage(content="Tell me about Vastbase", role="user"))
    fw_store.add_message(key, ChatMessage(
        content="Vastbase is a domestic database based on PostgreSQL.",
        role="assistant",
    ))

    # Turn 3
    fw_store.add_message(key, ChatMessage(content="Does it support vectors?", role="user"))
    fw_store.add_message(key, ChatMessage(
        content="Yes, Vastbase supports native vector search with HNSW and IVFFlat indexes.",
        role="assistant",
    ))

    messages = fw_store.get_messages(key)
    assert len(messages) == 6

    # User decides to delete the second turn's question (index 2)
    deleted = fw_store.delete_message(key, 2)
    assert deleted.content == "Tell me about Vastbase"

    # Also delete the corresponding answer (now at index 2 after shift)
    deleted = fw_store.delete_message(key, 2)
    assert "Vastbase is a domestic database" in deleted.content

    messages = fw_store.get_messages(key)
    assert len(messages) == 4
    assert messages[0].content == "Hi"
    assert messages[1].content == "Hello! How can I help?"
    assert messages[2].content == "Does it support vectors?"


# ---------------------------------------------------------------------------
# Scenario 3: Data persistence across reconnection
# ---------------------------------------------------------------------------

def test_data_persistence_across_reconnection(fw_store: VastbaseChatStore):
    """Messages should survive reconnection — create a new store instance
    pointing to the same collection and verify data is intact."""
    key = "persistence_test"

    # Write data with the first instance
    fw_store.set_messages(key, [
        ChatMessage(content="Persistent message 1", role="user"),
        ChatMessage(content="Persistent message 2", role="assistant"),
    ])

    # Create a NEW store instance (simulating reconnection)
    store2 = VastbaseChatStore.from_params(
        host=VASTBASE_CONFIG["host"],
        port=VASTBASE_CONFIG["port"],
        database=VASTBASE_CONFIG["database"],
        user=VASTBASE_CONFIG["user"],
        password=VASTBASE_CONFIG["password"],
        table_name=_make_table_name("fw_integration"),
    )

    # Verify data is accessible from the new instance
    messages = store2.get_messages(key)
    assert len(messages) == 2
    assert messages[0].content == "Persistent message 1"
    assert messages[1].content == "Persistent message 2"

    # Keys should include our key
    assert key in store2.get_keys()


# ---------------------------------------------------------------------------
# Scenario 4: from_uri factory method integration
# ---------------------------------------------------------------------------

def test_from_uri_factory_integration():
    """VastbaseChatStore.from_uri() should create a fully functional store."""
    uri = (
        f"vastbase://{VASTBASE_CONFIG['user']}:{VASTBASE_CONFIG['password']}"
        f"@{VASTBASE_CONFIG['host']}:{VASTBASE_CONFIG['port']}"
        f"/{VASTBASE_CONFIG['database']}"
    )
    table_name = _make_table_name("uri_integration")

    store = VastbaseChatStore.from_uri(uri, table_name=table_name)

    # Full CRUD cycle
    store.set_messages("uri_key", [
        ChatMessage(content="URI test message", role="user"),
    ])

    retrieved = store.get_messages("uri_key")
    assert len(retrieved) == 1
    assert retrieved[0].content == "URI test message"

    # Cleanup
    store.delete_messages("uri_key")


# ---------------------------------------------------------------------------
# Scenario 5: Async full lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_full_lifecycle(fw_store: VastbaseChatStore):
    """Complete async lifecycle: aset → aget → aadd → adelete → aget_keys."""
    key = "async_lifecycle"

    # Set messages
    await fw_store.aset_messages(key, [
        ChatMessage(content="Async msg 1", role="user"),
        ChatMessage(content="Async msg 2", role="assistant"),
    ])

    # Get messages
    messages = await fw_store.aget_messages(key)
    assert len(messages) == 2
    assert messages[0].content == "Async msg 1"

    # Add message
    await fw_store.async_add_message(key, ChatMessage(
        content="Async msg 3", role="user",
    ))
    messages = await fw_store.aget_messages(key)
    assert len(messages) == 3

    # Delete specific message
    deleted = await fw_store.adelete_message(key, 1)
    assert deleted.content == "Async msg 2"
    messages = await fw_store.aget_messages(key)
    assert len(messages) == 2

    # Delete last
    deleted = await fw_store.adelete_last_message(key)
    assert deleted.content == "Async msg 3"
    messages = await fw_store.aget_messages(key)
    assert len(messages) == 1

    # Get keys
    keys = await fw_store.aget_keys()
    assert key in keys

    # Delete all
    result = await fw_store.adelete_messages(key)
    assert result is not None
    assert len(result) == 1

    # Verify empty
    messages = await fw_store.aget_messages(key)
    assert messages == []


# ---------------------------------------------------------------------------
# Scenario 6: Large conversation history
# ---------------------------------------------------------------------------

def test_large_conversation_history(fw_store: VastbaseChatStore):
    """Store and retrieve a large conversation (100 messages) to verify
    scalability of the JSON serialization approach."""
    key = "large_history"

    messages = [
        ChatMessage(
            content=f"Message #{i}: " + "x" * 100,
            role="user" if i % 2 == 0 else "assistant",
        )
        for i in range(100)
    ]

    fw_store.set_messages(key, messages)

    retrieved = fw_store.get_messages(key)
    assert len(retrieved) == 100
    assert retrieved[0].content.startswith("Message #0:")
    assert retrieved[99].content.startswith("Message #99:")

    # Delete a middle message
    deleted = fw_store.delete_message(key, 50)
    assert deleted.content.startswith("Message #50:")
    assert len(fw_store.get_messages(key)) == 99
