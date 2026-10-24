from security.outbound_guard import OutboundGuard


def test_public_urls_with_path_segments_are_allowed():
    guard = OutboundGuard()
    assert guard.blocked("文档在 https://example.com/data/guide 里") is False
    assert guard.blocked("可以看 https://example.com/tmp/notes") is False


def test_local_paths_are_blocked():
    guard = OutboundGuard()
    assert guard.blocked(r"文件在 C:\Users\me\secret.txt") is True
    assert guard.blocked("路径 /app/data/media/x.jpg") is True
    assert guard.blocked("file:///app/data/secret") is True


def test_credentials_and_known_secrets_are_blocked():
    guard = OutboundGuard(["super-secret-token"])
    assert guard.blocked("super-secret-token") is True
    assert guard.blocked("api_key=abcdef123456") is True
    assert guard.blocked("这里的密码是 123456") is True
    assert guard.blocked("sk-abcdefgh12345678") is True


def test_normal_chat_is_not_blocked():
    guard = OutboundGuard(["super-secret-token"])
    assert guard.blocked("今天想聊什么？") is False
    assert guard.blocked("这个函数在 /tmp 下也能跑吗") is False
    assert guard.blocked("文件在 /tmp/secret.txt") is True
    assert guard.blocked("查看 https://docs.python.org/3/") is False
