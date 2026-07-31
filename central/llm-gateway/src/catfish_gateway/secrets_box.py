"""供应商 API key 的加密存储 (8/1, DESIGN-PROVIDER-SPLIT §5).

## 为什么 key 要从环境变量里搬出来

鸿波 7/30 问"填了就生效吗, 以后是不是不用改 .env"。地址可以, key 不行 ——
网关是在**每次调用时**做 `os.environ.get(变量名)`。而环境变量在**容器创建时**
固定, 所以:

  · 客户现场加一家新供应商 → 要改 .env **和** docker-compose.yml (那里是显式
    列名转发, 新变量容器根本看不见), 再 `docker compose up -d` 重建容器
  · 也就是说"在界面上加供应商"这件事在现架构下压根不成立

把 key 挪进库之后, 这条路才通, 而且**不需要重启** —— 配置系统 7/30 已经
热加载了 (TTL 3s + revision)。

## 主密钥

`CATFISH_SECRET_KEY`, 一次配好不再动。生成:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

用 Fernet (AES-128-CBC + HMAC-SHA256): 标准库级封装, 自带认证和时间戳,
不需要自己拼 nonce —— 自己拼 nonce 是这类代码最常见的出错点。

## 没配主密钥时**不能静默降级**

这是 7/30 反复撞到的那类问题的正面版本。三条硬规则:

  1. **读**: 解不开就返 None + 记日志, **不抛** —— 一个供应商的 key 解不开
     不该让整份配置挂掉 (冷启动时 lifespan 第一行就是 get_config(),
     抛出去 = 全站 502)。上层把这家标成不可用, 界面上说明原因。
  2. **写**: 主密钥没配就**拒绝保存**, 明确说要先配。不能让人以为存进去了 ——
     那会变成"填了 key、界面显示成功、但员工调用一直失败"。
  3. **永不回传**: 接口只返 has_key / key_source, 密文和明文都不出这个进程。

## 主密钥换了怎么办

换 key = 在界面上重填, 立即生效。
换**主密钥**要停机: 用老的全解、用新的全加。见 `rotate_master_key()`。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: 主密钥的环境变量名。**这是 .env 里唯一还需要的密钥**(除了数据库口令)。
MASTER_KEY_ENV = "CATFISH_SECRET_KEY"

_UNSET = object()
_CACHED: object = _UNSET


def _fernet():
    """拿 Fernet 实例. 没配 / 配错返 None.

    结果缓存 —— 每次解密都重建 Fernet 要跑一次 base64 解码 + key 派生,
    而模型配置每 3 秒重组一次、每次要解 N 个供应商的 key。

    ⚠ 缓存的是"读 env 的结果"。改主密钥必须重启进程 —— 这跟别的配置不同,
    但换主密钥本来就是停机操作 (见 rotate_master_key)。
    """
    global _CACHED
    if _CACHED is not _UNSET:
        return _CACHED

    raw = os.environ.get(MASTER_KEY_ENV, "").strip()
    if not raw:
        _CACHED = None
        return None
    try:
        from cryptography.fernet import Fernet  # noqa: PLC0415

        _CACHED = Fernet(raw.encode())
    except Exception:
        # 配了但格式不对 (少了几个字符 / 复制时带了引号) —— 这跟"没配"要
        # 区分开: 没配是还没做这一步, 配错是做了但做错了, 后者必须响。
        logger.error(
            "%s 配了但不是合法的 Fernet 密钥 —— 存库的 API key 全都解不开。"
            "它必须是 44 字符的 url-safe base64, 用这条生成: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"',
            MASTER_KEY_ENV,
        )
        _CACHED = None
    return _CACHED


def reset_cache() -> None:
    """清掉缓存. 只给测试用 —— 生产里改主密钥要重启进程。"""
    global _CACHED
    _CACHED = _UNSET


def is_configured() -> bool:
    """主密钥配好了没. 界面用它决定要不要允许填 key。"""
    return _fernet() is not None


def encrypt(plain: str) -> bytes:
    """加密一个 key. 主密钥没配就**抛** —— 写路径必须 fail loud.

    读路径可以降级 (解不开就标不可用), 写路径不行: 静默失败会变成
    "填了 key、界面显示成功、但员工调用一直失败", 而且没人知道去哪查。
    """
    f = _fernet()
    if f is None:
        raise RuntimeError(
            f"没有配置 {MASTER_KEY_ENV}, 无法加密保存 API key。\n\n"
            f"请让 IT 在服务器的 .env 里加一行 {MASTER_KEY_ENV}=…, 然后重启网关。\n"
            f'生成: python -c "from cryptography.fernet import Fernet; '
            f'print(Fernet.generate_key().decode())"\n\n'
            f"⚠ 这个值丢了的话, 所有存库的 API key 都解不开 —— "
            f"生成后请立刻存进密码管理器。"
        )
    if not plain:
        raise ValueError("要加密的 key 不能是空的")
    return f.encrypt(plain.encode())


def decrypt(cipher: bytes | memoryview | None) -> str | None:
    """解密一个 key. 解不开返 None (**不抛**).

    返 None 的三种情况, 上层都当"这家供应商的 key 不可用":
      · 主密钥没配 / 配错
      · 密文是用别的主密钥加的 (换过主密钥但没跑轮换脚本)
      · 密文被改坏了 (Fernet 自带认证, 改一个字节就解不开)

    不抛是因为冷启动时 lifespan 第一行就是 get_config() —— 一个供应商的
    key 解不开不该让整个网关起不来 (同 7/30 那条 ${VAR} 的教训)。
    """
    if not cipher:
        return None
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(bytes(cipher)).decode()
    except Exception:
        logger.error(
            "有 API key 解不开 —— 可能是 %s 换过但没跑轮换脚本, 或者密文被改坏了。"
            "用它的模型现在不可用。",
            MASTER_KEY_ENV,
        )
        return None


def rotate_master_key(old_key: str, new_key: str) -> int:
    """换主密钥: 用老的全解、用新的全加. 返回换掉的条数.

    **停机操作。** 跑之前停网关, 跑完把 .env 里的 CATFISH_SECRET_KEY 换成
    新值再起。跑到一半失败的话事务回滚, 库里还是老密文, 用老 key 能起来。

    写在这里而不是等真要换的时候现想 —— 加密方案落地时就该把退出路径备好,
    否则"换主密钥"会变成一件没人敢做的事。
    """
    from cryptography.fernet import Fernet  # noqa: PLC0415

    from .model_store import _bump_revision, _conn, is_enabled  # noqa: PLC0415

    if not is_enabled():
        raise RuntimeError("未配置 CATFISH_DB_URL")
    old_f, new_f = Fernet(old_key.encode()), Fernet(new_key.encode())

    n = 0
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, api_key_enc FROM gateway_providers WHERE api_key_enc IS NOT NULL"
        )
        for pid, enc in cur.fetchall():
            plain = old_f.decrypt(bytes(enc))  # 解不开就抛, 整个事务回滚
            cur.execute(
                "UPDATE gateway_providers SET api_key_enc = %s, "
                "updated_by = 'rotate:master-key', updated_at = now() WHERE id = %s",
                (new_f.encrypt(plain), pid),
            )
            n += 1
        if n:
            _bump_revision(cur)
        conn.commit()
    return n
