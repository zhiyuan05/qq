"""管理员直连动作识别测试。"""
from core.admin_direct import AdminDirectAgent


def test_admin_action_phrases_match():
    agent = AdminDirectAgent()

    assert agent.matches("把昵称改成小明")
    assert agent.matches("签名改成今天也在写代码")
    assert agent.matches("看看好友列表")
    assert agent.matches("列出所有群")
    assert agent.matches("设置在线状态为忙碌")
    assert agent.matches("新建一个人设叫冷面助手")
    assert agent.matches("把猫娘的人设正文改一下")
    assert agent.matches("重启一下机器人")
    assert agent.matches("看看服务状态")
    assert agent.matches("改一下配置")


def test_plain_chat_does_not_match():
    agent = AdminDirectAgent()

    assert not agent.matches("今天好累")
    assert not agent.matches("你是谁")
    assert not agent.matches("切换到猫娘")
    assert not agent.matches("我喜欢小明这个名字")
    assert not agent.matches("这个配置挺好看的")


def test_questions_and_negations_do_not_match():
    agent = AdminDirectAgent()

    assert not agent.matches("怎么改昵称")
    assert not agent.matches("如何设置签名")
    assert not agent.matches("不要重启机器人")
    assert not agent.matches("别删人设")
    assert not agent.matches("")
    assert not agent.matches("改昵称" + "啊" * 300)
