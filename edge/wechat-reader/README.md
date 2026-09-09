# Catfish chat export reader

This package reads a chat export file explicitly selected by the employee. It
does not inspect the WeChat database, extract keys, modify WeChat, call a model,
use the network, or persist a plaintext index.

Only JSON, JSONL and CSV export analysis is supported. The experimental database
selection and automatic-access Provider implementation has been removed. Old
database-source configurations are not used; select an export file to enable
analysis again. No user database or export file is deleted by this removal.

The Companion file picker accepts JSON, JSONL, and CSV. A message needs these
logical fields:

| Field | Required | Accepted examples |
|---|---:|---|
| session ID or name | yes | `session_id`, `chat_id`, `talker`, `会话ID`, `会话名称` |
| timestamp | yes | `timestamp`, `time`, `create_time`, `时间`, `发送时间` |
| message text | no | `text`, `content`, `message`, `内容`, `消息内容` |
| sender | no | `sender_name`, `sender`, `nickname`, `发送人`, `发送者` |
| message ID | no | `message_id`, `msg_id`, `id`, `消息ID` |
| sent by employee | no | `is_self`, `outgoing`, `是否本人`, `是否自己` |

JSON may be an array of message objects or an object with a `messages` array.
JSONL uses one message object per line. CSV must contain a header row. See
[`examples/chat-export-template.csv`](examples/chat-export-template.csv).

The reader is packaged independently and installed into the Hermes virtual
environment. The stable macOS entry point is
`~/.catfish/bin/catfish-wechat-reader`; Windows uses the executable in
`%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts`.

Build the wheel without network access or packaging dependencies:

```bash
python3 scripts/build_wheel.py --output-dir /tmp/catfish-wechat-reader-dist
```
