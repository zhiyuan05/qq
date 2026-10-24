from core.context_assembler import ContextAssembler
from core.session import ChatMessage
from utils.config import ContextConfig


def _fixed_counter(per_message: int):
    def counter(messages, image_token_cost=800):
        return per_message * len(messages)

    return counter


def _config(**overrides) -> ContextConfig:
    values = {"max_prompt_tokens": 512, "recent_messages": 12}
    values.update(overrides)
    return ContextConfig(**values)


def test_newest_history_is_kept_when_budget_is_tight():
    assembler = ContextAssembler(_config(), counter=_fixed_counter(100))
    history = [ChatMessage("user", f"h{index}") for index in range(5)]

    messages = assembler.assemble(system_content="system", user_message="current", history=history)

    contents = [message["content"] for message in messages]
    assert contents[0] == "system"
    assert contents[-1] == "current"
    assert contents[1:-1] == ["h2", "h3", "h4"]


def test_summary_survives_long_history_and_latest_exchange_is_kept():
    assembler = ContextAssembler(_config(), counter=_fixed_counter(100))
    history = [ChatMessage("user" if index % 2 == 0 else "assistant", f"h{index}") for index in range(8)]

    messages = assembler.assemble(
        system_content="system",
        user_message="current",
        history=history,
        summary="summary",
    )

    contents = [message["content"] for message in messages]
    assert any(str(content).startswith("之前的对话摘要") for content in contents)
    assert contents[-1] == "current"
    assert "h7" in contents


def test_current_message_and_system_survive_an_impossible_budget():
    assembler = ContextAssembler(_config(), counter=_fixed_counter(1000))

    messages = assembler.assemble(
        system_content="system",
        user_message="current",
        history=[ChatMessage("user", "old")],
        summary="summary",
        memory_results=[{"text": "memory"}],
    )

    assert [message["content"] for message in messages] == ["system", "current"]


def test_summary_outranks_traits_and_memory():
    assembler = ContextAssembler(_config(), counter=_fixed_counter(100))

    messages = assembler.assemble(
        system_content="s",
        user_message="u",
        summary="summary",
        user_traits=["trait"],
        global_traits=["global"],
        memory_results=[{"text": "memory"}],
    )

    contents = [message["content"] for message in messages]
    assert any(content.startswith("之前的对话摘要") for content in contents)
    assert not any(content.startswith("相关记忆") for content in contents)


def test_summary_and_trait_token_caps_are_enforced():
    def counter(messages, image_token_cost=800):
        return sum(len(str(message.get("content", ""))) for message in messages)

    assembler = ContextAssembler(
        _config(summary_tokens=25, user_trait_tokens=30, global_trait_tokens=30),
        counter=counter,
    )

    messages = assembler.assemble(
        system_content="s",
        user_message="u",
        summary="摘" * 200,
        user_traits=["短特质", "很长的特质" * 20],
    )

    summary = next(message for message in messages if str(message["content"]).startswith("之前的对话摘要"))
    assert len(summary["content"]) <= 25
    traits = next(message for message in messages if "互动特征" in str(message["content"]))
    assert "短特质" in traits["content"]
    assert "很长的特质" not in traits["content"]


def test_history_image_window_limits_reattached_images():
    calls = []

    def image_builder(message):
        calls.append(message.content)
        return [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,x"}}]

    assembler = ContextAssembler(
        _config(),
        counter=_fixed_counter(10),
        image_builder=image_builder,
        history_image_window=1,
    )
    from platforms.models import Attachment, MessageType

    history = [
        ChatMessage("user", "old image", attachments=[Attachment(MessageType.IMAGE, "/tmp/a.jpg")]),
        ChatMessage("user", "new image", attachments=[Attachment(MessageType.IMAGE, "/tmp/b.jpg")]),
    ]

    messages = assembler.assemble(system_content="s", user_message="u", history=history)

    assert calls == ["new image"]
    assert isinstance(messages[-2]["content"], list)


def test_image_blocks_use_a_fixed_cost():
    def counter(messages, image_token_cost=800):
        total = 0
        for message in messages:
            content = message["content"]
            if isinstance(content, list):
                total += sum(image_token_cost for block in content if block.get("type") == "image_url")
        return total

    assembler = ContextAssembler(_config(), counter=counter)
    image_message = [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "a" * 100}},
    ]

    messages = assembler.assemble(system_content="s", user_message=image_message)

    assert messages[-1]["content"] is image_message
