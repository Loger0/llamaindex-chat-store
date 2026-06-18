#!/usr/bin/env python3
"""Demo: VastbaseChatStore — LlamaIndex Chat History 完整工作流.

真实用户场景：使用 VastbaseChatStore 作为 LlamaIndex 聊天历史存储后端，
演示完整的 CRUD + ChatMemoryBuffer 集成 + 多会话管理生命周期.

用法:
    export VASTBASE_HOST=172.16.105.107
    export VASTBASE_PORT=15432
    export VASTBASE_DATABASE=vastbase
    export VASTBASE_USER=aidev
    export VASTBASE_PASSWORD=Vbase_123456
    python tests/demo_chat_store.py
"""

import asyncio
import os
import sys

from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.storage.chat_store.vastbase import VastbaseChatStore


VASTBASE_HOST = os.environ.get("VASTBASE_HOST", "172.16.105.107")
VASTBASE_PORT = int(os.environ.get("VASTBASE_PORT", "15432"))
VASTBASE_DATABASE = os.environ.get("VASTBASE_DATABASE", "vastbase")
VASTBASE_USER = os.environ.get("VASTBASE_USER", "aidev")
VASTBASE_PASSWORD = os.environ.get("VASTBASE_PASSWORD", "Vbase_123456")
TABLE_NAME = "demo_chatstore"


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def demo_scenario_1_basic_crud():
    """Scenario 1: 基础 CRUD 操作."""
    print_section("📋 Scenario 1: 基础 CRUD 操作")

    store = VastbaseChatStore.from_params(
        host=VASTBASE_HOST,
        port=VASTBASE_PORT,
        database=VASTBASE_DATABASE,
        user=VASTBASE_USER,
        password=VASTBASE_PASSWORD,
        table_name=TABLE_NAME,
    )
    print("   ✅ VastbaseChatStore 创建成功")

    key = "demo_basic_crud"

    # SET: 创建初始对话
    print("\n   📝 Step 1: set_messages — 创建初始对话...")
    store.set_messages(key, [
        ChatMessage(content="你好，请介绍一下 Vastbase。", role="user"),
        ChatMessage(
            content="Vastbase G100 是海量数据推出的国产商业数据库，基于 PostgreSQL 内核，"
                    "支持原生向量检索。",
            role="assistant",
        ),
    ])
    print("   ✅ 已写入 2 条消息")

    # GET: 获取消息
    print("\n   📖 Step 2: get_messages — 获取消息...")
    messages = store.get_messages(key)
    assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
    for i, msg in enumerate(messages):
        print(f"   [{i}] {msg.role}: {msg.content[:60]}...")
    print("   ✅ 获取到 2 条消息")

    # ADD: 追加消息
    print("\n   ➕ Step 3: add_message — 追加一条消息...")
    store.add_message(key, ChatMessage(
        content="Vastbase 支持哪些向量索引？", role="user",
    ))
    messages = store.get_messages(key)
    assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"
    print(f"   ✅ 现在有 {len(messages)} 条消息")

    # DELETE_MESSAGE: 按索引删除
    print("\n   🗑️  Step 4: delete_message — 删除索引 1 的消息...")
    deleted = store.delete_message(key, 1)
    assert deleted is not None, "Expected deleted message, got None"
    print(f"   ✅ 已删除: {deleted.content[:60]}...")
    messages = store.get_messages(key)
    assert len(messages) == 2, f"Expected 2 messages after delete, got {len(messages)}"

    # DELETE_LAST: 删除最后一条
    print("\n   🗑️  Step 5: delete_last_message — 删除最后一条消息...")
    deleted = store.delete_last_message(key)
    assert deleted is not None, "Expected deleted last message, got None"
    print(f"   ✅ 已删除: {deleted.content[:60]}...")

    # DELETE_MESSAGES: 删除全部
    print("\n   🗑️  Step 6: delete_messages — 删除该 key 全部消息...")
    result = store.delete_messages(key)
    assert result is not None, "Expected deleted messages, got None"
    assert store.get_messages(key) == [], "Expected empty messages after delete_all"
    print("   ✅ 全部消息已删除")

    print("\n   ✅ Scenario 1 通过: 基础 CRUD 操作正常")
    return True


def demo_scenario_2_multi_session():
    """Scenario 2: 多会话管理."""
    print_section("📋 Scenario 2: 多会话管理（多 key 隔离）")

    store = VastbaseChatStore.from_params(
        host=VASTBASE_HOST,
        port=VASTBASE_PORT,
        database=VASTBASE_DATABASE,
        user=VASTBASE_USER,
        password=VASTBASE_PASSWORD,
        table_name=TABLE_NAME,
    )

    # 创建多个会话
    sessions = {
        "user_alice": [
            ChatMessage(content="Alice 的第一个问题", role="user"),
            ChatMessage(content="给 Alice 的回答", role="assistant"),
        ],
        "user_bob": [
            ChatMessage(content="Bob 的第一个问题", role="user"),
        ],
        "user_charlie": [
            ChatMessage(content="Charlie 的第一个问题", role="user"),
            ChatMessage(content="给 Charlie 的回答", role="assistant"),
            ChatMessage(content="Charlie 的追问", role="user"),
        ],
    }

    print("\n   📝 创建 3 个独立会话...")
    for key, msgs in sessions.items():
        store.set_messages(key, msgs)
        print(f"   - {key}: {len(msgs)} 条消息")

    # get_keys
    print("\n   🔑 get_keys — 列出所有会话...")
    keys = store.get_keys()
    for k in ["user_alice", "user_bob", "user_charlie"]:
        assert k in keys, f"Expected key '{k}' not found in {keys}"
    print(f"   ✅ 找到 {len(keys)} 个会话 key")

    # 验证隔离性
    print("\n   🔒 验证会话隔离性...")
    alice_msgs = store.get_messages("user_alice")
    bob_msgs = store.get_messages("user_bob")
    assert len(alice_msgs) == 2, f"Alice should have 2, got {len(alice_msgs)}"
    assert len(bob_msgs) == 1, f"Bob should have 1, got {len(bob_msgs)}"
    print("   ✅ 各会话数据互相隔离")

    # 删除一个会话
    print("\n   🗑️  删除 user_bob 会话...")
    store.delete_messages("user_bob")
    assert store.get_messages("user_bob") == [], "Bob's messages should be empty"
    assert len(store.get_messages("user_alice")) == 2, "Alice's messages should be intact"
    print("   ✅ 删除不影响其他会话")

    # Cleanup
    for key in ["user_alice", "user_charlie"]:
        store.delete_messages(key)

    print("\n   ✅ Scenario 2 通过: 多会话管理正常")
    return True


def demo_scenario_3_chat_memory_buffer():
    """Scenario 3: LlamaIndex ChatMemoryBuffer 集成."""
    print_section("📋 Scenario 3: LlamaIndex ChatMemoryBuffer 集成")

    store = VastbaseChatStore.from_params(
        host=VASTBASE_HOST,
        port=VASTBASE_PORT,
        database=VASTBASE_DATABASE,
        user=VASTBASE_USER,
        password=VASTBASE_PASSWORD,
        table_name=TABLE_NAME,
    )

    # 创建 ChatMemoryBuffer
    print("\n   🧠 创建 ChatMemoryBuffer (使用 VastbaseChatStore)...")
    memory = ChatMemoryBuffer.from_defaults(
        chat_store=store,
        chat_store_key="demo_memory",
        token_limit=3000,
    )
    print("   ✅ ChatMemoryBuffer 创建成功")

    # 模拟对话
    print("\n   💬 模拟多轮对话...")
    conversations = [
        ("user", "你好，我需要一个数据库方案。"),
        ("assistant", "好的，请问您的使用场景是什么？"),
        ("user", "主要是 OLTP + 向量搜索。"),
        ("assistant", "推荐 Vastbase G100，它原生支持向量检索。"),
        ("user", "性能如何？"),
    ]
    for role, content in conversations:
        memory.put(ChatMessage(content=content, role=role))
        print(f"   [{role}] {content}")

    # 获取历史
    print("\n   📖 获取完整对话历史...")
    all_messages = memory.get_all()
    assert len(all_messages) == 5, f"Expected 5, got {len(all_messages)}"
    print(f"   ✅ 共 {len(all_messages)} 条消息")

    # 验证数据持久化到 Vastbase
    print("\n   💾 验证数据已持久化到 Vastbase...")
    stored = store.get_messages("demo_memory")
    assert len(stored) == 5, f"Expected 5 stored, got {len(stored)}"
    print("   ✅ 数据已持久化")

    # Reset
    print("\n   🔄 Reset 对话历史...")
    memory.reset()
    assert memory.get_all() == [], "Expected empty after reset"
    assert store.get_messages("demo_memory") == [], "Store should be empty after reset"
    print("   ✅ 历史已清除")

    print("\n   ✅ Scenario 3 通过: ChatMemoryBuffer 集成正常")
    return True


def demo_scenario_4_message_types():
    """Scenario 4: 复杂消息类型 (metadata, roles, multimodal)."""
    print_section("📋 Scenario 4: 复杂消息类型")

    store = VastbaseChatStore.from_params(
        host=VASTBASE_HOST,
        port=VASTBASE_PORT,
        database=VASTBASE_DATABASE,
        user=VASTBASE_USER,
        password=VASTBASE_PASSWORD,
        table_name=TABLE_NAME,
    )

    key = "demo_complex_msgs"

    # 所有角色类型
    print("\n   🎭 测试所有 MessageRole 类型...")
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="You are a helpful assistant."),
        ChatMessage(role=MessageRole.USER, content="What is Vastbase?"),
        ChatMessage(role=MessageRole.ASSISTANT, content="Vastbase is a database..."),
        ChatMessage(role=MessageRole.FUNCTION, content='{"result": "ok"}'),
        ChatMessage(role=MessageRole.TOOL, content="Tool output data"),
    ]
    store.set_messages(key, messages)
    retrieved = store.get_messages(key)
    assert len(retrieved) == 5
    for orig, ret in zip(messages, retrieved):
        assert orig.role == ret.role, f"Role mismatch: {orig.role} != {ret.role}"
        assert orig.content == ret.content, f"Content mismatch"
    print("   ✅ 所有 5 种 MessageRole 类型 round-trip 正常")

    # additional_kwargs
    print("\n   📎 测试 additional_kwargs (metadata)...")
    key2 = "demo_metadata"
    msg_with_kwargs = ChatMessage(
        role="assistant",
        content="Response with tool calls",
        additional_kwargs={
            "tool_calls": [
                {"id": "call_1", "name": "search", "args": {"query": "vector db"}}
            ],
            "model": "gpt-4",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )
    store.set_messages(key2, [msg_with_kwargs])
    retrieved = store.get_messages(key2)
    assert len(retrieved) == 1
    assert retrieved[0].additional_kwargs["tool_calls"][0]["name"] == "search"
    assert retrieved[0].additional_kwargs["model"] == "gpt-4"
    print("   ✅ additional_kwargs 完整保留")

    # Cleanup
    store.delete_messages(key)
    store.delete_messages(key2)

    print("\n   ✅ Scenario 4 通过: 复杂消息类型正常")
    return True


async def demo_scenario_5_async():
    """Scenario 5: 异步 API 完整生命周期."""
    print_section("📋 Scenario 5: 异步 API 完整生命周期")

    store = VastbaseChatStore.from_params(
        host=VASTBASE_HOST,
        port=VASTBASE_PORT,
        database=VASTBASE_DATABASE,
        user=VASTBASE_USER,
        password=VASTBASE_PASSWORD,
        table_name=TABLE_NAME,
    )

    key = "demo_async"

    # Async set
    print("\n   ⚡ aset_messages...")
    await store.aset_messages(key, [
        ChatMessage(content="Async 消息 1", role="user"),
        ChatMessage(content="Async 回复 1", role="assistant"),
    ])
    print("   ✅ 已写入")

    # Async get
    print("\n   ⚡ aget_messages...")
    messages = await store.aget_messages(key)
    assert len(messages) == 2
    print(f"   ✅ 获取到 {len(messages)} 条消息")

    # Async add
    print("\n   ⚡ async_add_message...")
    await store.async_add_message(key, ChatMessage(
        content="Async 追加消息", role="user",
    ))
    messages = await store.aget_messages(key)
    assert len(messages) == 3
    print(f"   ✅ 追加后共 {len(messages)} 条消息")

    # Async delete specific
    print("\n   ⚡ adelete_message...")
    deleted = await store.adelete_message(key, 1)
    assert deleted.content == "Async 回复 1"
    print(f"   ✅ 已删除索引 1")

    # Async delete last
    print("\n   ⚡ adelete_last_message...")
    deleted = await store.adelete_last_message(key)
    assert deleted.content == "Async 追加消息"
    print(f"   ✅ 已删除最后一条")

    # Async get keys
    print("\n   ⚡ aget_keys...")
    keys = await store.aget_keys()
    assert key in keys
    print(f"   ✅ 找到 {len(keys)} 个 key")

    # Async delete all
    print("\n   ⚡ adelete_messages...")
    result = await store.adelete_messages(key)
    assert result is not None
    messages = await store.aget_messages(key)
    assert messages == []
    print("   ✅ 全部删除完成")

    print("\n   ✅ Scenario 5 通过: 异步 API 完整生命周期正常")
    return True


def demo_scenario_6_from_uri():
    """Scenario 6: from_uri 工厂方法."""
    print_section("📋 Scenario 6: from_uri 工厂方法")

    uri = (
        f"vastbase://{VASTBASE_USER}:{VASTBASE_PASSWORD}"
        f"@{VASTBASE_HOST}:{VASTBASE_PORT}/{VASTBASE_DATABASE}"
    )
    print(f"\n   🔗 URI: vastbase://...@{VASTBASE_HOST}:{VASTBASE_PORT}/...")

    store = VastbaseChatStore.from_uri(uri, table_name=TABLE_NAME)
    print("   ✅ VastbaseChatStore.from_uri() 创建成功")

    key = "demo_uri"
    store.set_messages(key, [
        ChatMessage(content="URI 创建的消息", role="user"),
    ])
    messages = store.get_messages(key)
    assert len(messages) == 1
    assert messages[0].content == "URI 创建的消息"
    print("   ✅ CRUD 操作正常")

    store.delete_messages(key)

    print("\n   ✅ Scenario 6 通过: from_uri 工厂方法正常")
    return True


def main():
    """Run all demo scenarios."""
    print("=" * 60)
    print("🚀 Demo: VastbaseChatStore — LlamaIndex Chat History 工作流")
    print("=" * 60)
    print(f"   连接: {VASTBASE_HOST}:{VASTBASE_PORT}/{VASTBASE_DATABASE}")
    print(f"   表名: {TABLE_NAME}")

    results = []

    # Sync scenarios
    scenarios = [
        ("基础 CRUD 操作", demo_scenario_1_basic_crud),
        ("多会话管理", demo_scenario_2_multi_session),
        ("ChatMemoryBuffer 集成", demo_scenario_3_chat_memory_buffer),
        ("复杂消息类型", demo_scenario_4_message_types),
        ("from_uri 工厂方法", demo_scenario_6_from_uri),
    ]

    for name, func in scenarios:
        try:
            ok = func()
            results.append((name, ok))
        except Exception as e:
            print(f"\n   ❌ Scenario '{name}' 失败: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Async scenario
    try:
        ok = asyncio.get_event_loop().run_until_complete(demo_scenario_5_async())
        results.append(("异步 API 完整生命周期", ok))
    except Exception as e:
        print(f"\n   ❌ Scenario '异步 API' 失败: {e}")
        import traceback
        traceback.print_exc()
        results.append(("异步 API 完整生命周期", False))

    # Summary
    print(f"\n{'=' * 60}")
    print("📊 Demo 结果汇总")
    print(f"{'=' * 60}")
    all_pass = True
    for name, ok in results:
        status = "✅" if ok else "❌"
        print(f"   {status} {name}")
        if not ok:
            all_pass = False

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n   总计: {passed}/{total} 场景通过")

    if all_pass:
        print(f"\n{'=' * 60}")
        print("✅ Demo 全部通过：VastbaseChatStore 完整工作流正常")
        print(f"{'=' * 60}")
    else:
        print(f"\n{'=' * 60}")
        print("❌ Demo 有场景失败，请检查上方错误信息")
        print(f"{'=' * 60}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
