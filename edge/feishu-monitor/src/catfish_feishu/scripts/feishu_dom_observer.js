// catfish-feishu-monitor · DOM Observer
//
// 注入到飞书 Web 页面，用 MutationObserver 监听新消息到达，
// 结构化后通过 window.__catfishEmit(jsonStr) 回传给 Python。
//
// 设计点：
//   1. 幂等：被注入多次时只保留最新一份 observer
//   2. 防御性 selector：飞书 Web DOM 变化时不会整个挂掉，只是匹配不到
//   3. 消息去重：内存里维护已处理消息 ID 集合
//
// 周一实测任务：打开飞书 Web 用 DevTools 确认以下 selector：
//   - MESSAGE_LIST_SELECTOR: 聊天消息列表容器
//   - MESSAGE_ITEM_SELECTOR: 单条消息节点
//   - SENDER_SELECTOR / TEXT_SELECTOR / TIMESTAMP_SELECTOR: 字段提取
//   - CONVERSATION_TITLE_SELECTOR: 当前会话标题（群名 / DM 对方）

(function () {
  'use strict';

  // 幂等：如果之前注入过，先清理
  if (window.__catfishObserver) {
    try { window.__catfishObserver.disconnect(); } catch (e) {}
    console.info('[catfish] 清理旧 observer');
  }

  // ---- 配置 ----
  // 这些 selector 是第一版猜测，周一要基于飞书 Web 实际 DOM 校准
  // 已经放了多个候选做 fallback，第一个匹配即用
  const SELECTORS = {
    messageList: [
      '[class*="chat-messages-list"]',
      '[class*="message-list"]',
      '[data-test*="messageList"]',
      '.lark-im-messages',
    ],
    messageItem: [
      '[class*="message-item"]',
      '[data-test*="messageItem"]',
      '[class*="msg-item"]',
    ],
    messageSender: [
      '[class*="sender-name"]',
      '[class*="message-sender"]',
      '[data-test*="sender"]',
    ],
    messageText: [
      '[class*="message-content"] [class*="text"]',
      '[class*="message-body"]',
      '[class*="msg-body"]',
    ],
    messageTimestamp: [
      '[class*="message-time"]',
      'time',
      '[class*="timestamp"]',
    ],
    conversationTitle: [
      '[class*="conversation-title"]',
      '[class*="chat-header-title"]',
      '[class*="chat-title"]',
      'h1',
    ],
  };

  function firstMatch(root, selectorList) {
    for (const sel of selectorList) {
      const el = root.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function extractMessage(node) {
    // 尝试从这条消息节点提取结构化字段
    // 每个字段都容错：找不到返回空字符串而非 null
    const senderEl = firstMatch(node, SELECTORS.messageSender);
    const textEl = firstMatch(node, SELECTORS.messageText);
    const timeEl = firstMatch(node, SELECTORS.messageTimestamp);

    return {
      sender: senderEl ? senderEl.textContent.trim() : '',
      text: textEl ? textEl.textContent.trim() : node.textContent.trim().slice(0, 500),
      timestamp_display: timeEl ? timeEl.textContent.trim() : '',
      node_signature: node.textContent.trim().slice(0, 100),
    };
  }

  function extractConversation() {
    // 当前聊天窗口的标题
    const el = firstMatch(document, SELECTORS.conversationTitle);
    return el ? el.textContent.trim() : '';
  }

  function isDirectMessage() {
    // 飞书 DM 通常 URL 或标题有特征。先粗略判断：
    // 群聊标题常带"群"或成员数标识；DM 标题就是对方姓名
    // 周一实测校准
    const title = extractConversation();
    const url = window.location.href;
    // URL 有 /im/ 或 /chat/ 且不带群 ID 特征——先粗略
    return !/群|\(\d+\)/.test(title);
  }

  // 简单的发送绑定器：调用 Python 回来的 __catfishEmit
  function emit(payload) {
    if (typeof window.__catfishEmit === 'function') {
      try {
        window.__catfishEmit(JSON.stringify(payload));
      } catch (e) {
        console.warn('[catfish] emit 失败', e);
      }
    } else {
      // binding 还没注册好，落地到 buffer 里
      (window.__catfishPendingEvents = window.__catfishPendingEvents || []).push(payload);
    }
  }

  // 去重：同一条消息只发一次
  const seen = new Set();
  function signature(m) {
    return [m.sender, m.timestamp_display, m.node_signature].join('|');
  }

  function handleNewMessageNode(node) {
    // 只处理"真正的消息节点"，MutationObserver 可能触发很多无关变化
    const msg = extractMessage(node);
    if (!msg.text && !msg.sender) return;
    const sig = signature(msg);
    if (seen.has(sig)) return;
    seen.add(sig);

    const conversation = extractConversation();
    const is_dm = isDirectMessage();
    const payload = {
      type: 'new_message',
      conversation,
      sender: msg.sender,
      text: msg.text,
      timestamp: Date.now() / 1000,
      timestamp_display: msg.timestamp_display,
      is_dm,
      url: window.location.href,
    };
    emit(payload);
  }

  function scanInitial() {
    // 注入时扫一遍已有消息做基线（但不发 event，避免历史消息灌爆）
    const list = firstMatch(document, SELECTORS.messageList);
    if (!list) {
      console.warn('[catfish] 初始扫描未找到 message list');
      return 0;
    }
    // 预填 seen 集合，这样后续真 mutation 来新消息才会 emit
    for (const sel of SELECTORS.messageItem) {
      const nodes = list.querySelectorAll(sel);
      for (const n of nodes) {
        const m = extractMessage(n);
        seen.add(signature(m));
      }
    }
    return seen.size;
  }

  function startObserver() {
    const list = firstMatch(document, SELECTORS.messageList);
    if (!list) {
      console.warn('[catfish] 消息列表未出现，1 秒后重试');
      setTimeout(startObserver, 1000);
      return;
    }
    const baseline = scanInitial();
    console.info('[catfish] observer 启动，已知历史消息', baseline, '条');

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          // 如果 n 本身是消息节点，或者包含消息节点
          for (const sel of SELECTORS.messageItem) {
            if (n.matches && n.matches(sel)) {
              handleNewMessageNode(n);
            } else if (n.querySelectorAll) {
              for (const child of n.querySelectorAll(sel)) {
                handleNewMessageNode(child);
              }
            }
          }
        }
      }
    });

    observer.observe(list, {
      childList: true,
      subtree: true,
    });

    window.__catfishObserver = observer;
    emit({ type: 'observer_ready', baseline, conversation: extractConversation() });
  }

  startObserver();
})();
