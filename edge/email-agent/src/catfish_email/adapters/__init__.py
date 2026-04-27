"""邮件客户端适配器集合。每个 client × OS 一个 adapter, 共用 EmailAdapter ABC。

工厂入口在 catfish_email.inbox.get_adapter, 不直接 import 子模块。
"""
