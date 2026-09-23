# Catfish chat export reader

This package reads a chat export file explicitly selected by the employee. It
does not inspect the WeChat database, extract keys, modify WeChat, call a model,
use the network, or persist a plaintext index.

Supported sources: WeChat's native "合并转发 → 其他应用" ZIP (see below), plus
JSON, JSONL and CSV exports. The experimental database
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

## WeChat native ZIP exports

macOS WeChat 4.1.13+ can merge-forward selected messages to another app; it
hands over a ZIP with `聊天记录.txt` plus `聊天记录内的图片、视频和文件/`. The
TXT is four lines per message (`·sender`, `2026年3月25日 19:34`, body, blank).
Parsing rules follow WeChatBridge (MIT, github.com/freestylefly/WeChatBridge)
and were checked against real private-chat and group-chat exports.

- Read in memory only; encrypted entries, >1000 entries, >1 GB expanded,
  >16 MB transcript and unsafe paths are rejected.
- Only tags seen in real exports get a type (`[图片]`, `[文件]`, `[小程序]`,
  `[语音通话]`); anything else, e.g. the `[OK]` emoji shorthand, stays text.
  Archive attachments (zip/rar) are not included by WeChat and are reported
  as `attachment_present: false`.
- A transcript whose first record does not start at byte 0 is rejected
  (`unsupported_format`) instead of being guessed.

Exports accumulate in an import library chosen by the caller:

```bash
catfish-wechat-reader inspect --json --source x.zip [--library DIR]   # read-only preview
catfish-wechat-reader import  --json --source x.zip --library DIR \
    [--group-id ID | --group-name NAME] [--self-name NAME]
catfish-wechat-reader groups        --json --library DIR
catfish-wechat-reader update-group  --json --library DIR --group-id ID [--name N] [--self-name S]
catfish-wechat-reader remove-group  --json --library DIR --group-id ID
catfish-wechat-reader sessions|history|search --source DIR ...        # library as a source
```

The library holds only the employee's original ZIPs (named by sha256), one
sidecar per ZIP (the import commit point) and `groups.json` (name, members,
self name). No derived plaintext index is written. The TXT carries no group
name, message ID or self marker: group names are supplied once by the
employee, later exports are matched by sender overlap (self excluded), and
message IDs hash group/sender/minute/text/occurrence so overlapping exports
de-duplicate.

The reader is packaged independently and installed into the Hermes virtual
environment. The stable macOS entry point is
`~/.catfish/bin/catfish-wechat-reader`; Windows uses the executable in
`%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts`.

Build the wheel without network access or packaging dependencies:

```bash
python3 scripts/build_wheel.py --output-dir /tmp/catfish-wechat-reader-dist
```
