# 魔珐 Web SDK 接入确认

更新时间：2026-10-13

## 已确认配置

- 角色：吴佳
- 普通话音色：温柔小欣
- 表演风格：通用

本文件不记录 App ID 或 App Secret。

## 本次核对的新版官方文档

- [1.1 概述](https://xingyun3d.com/developers/67-541)
- [1.2 快速开始](https://xingyun3d.com/developers/67-543)
- [1.3 安装与集成](https://xingyun3d.com/developers/67-544)
- [3.1 构造参数](https://xingyun3d.com/developers/69-557)
- [3.4 语音与对话方法](https://xingyun3d.com/developers/69-560)
- [3.5 具身智能体控制方法](https://xingyun3d.com/developers/69-561)
- [3.6 回调接口](https://xingyun3d.com/developers/69-562)
- [3.8 类型定义](https://xingyun3d.com/developers/69-564)
- [常见问题（FAQ）](https://xingyun3d.com/developers/70-565)

## 已确认结论

### 1. SDK 已升级为端到端智能体

新版使用 `XingyunAvatarAgent`，CDN 示例地址为：

`https://media.xingyun3d.com/xingyun3d/general/litesdk/xmovAvatar_e2e@latest.js`

它将语音识别、Brain 大模型、数字人播报、状态和打断集中在同一个对象中。快速体验可以使用 `@latest`，官方建议生产环境锁定具体版本。

### 2. App Secret 的官方定位与项目约束冲突

官方构造参数把 `appSecret` 定义为“与 `appId` 配套的浏览器侧签名凭证”。快速开始、Vanilla、Vue 和 React 示例都直接在浏览器创建实例时传入 `appId` 和 `appSecret`。

官方 FAQ 同时明确：

- `appSecret` 设计上需要下发给前端；
- 它仍属于敏感信息；
- 不应提交到公开仓库、写入日志或跨域暴露；
- 浏览器不是密钥存储，前端代码、构建产物和 `localStorage` 都可被查看；
- 高权限服务端 API Key 不应放进 `asr_config` 或 `brain_config`。

公开文档没有提供适用于公共网关的“服务端换取短期 Token、前端不接触 App Secret”流程。构造参数中的 `e2eServer + authToken` 被标注为私有化或调试用途，不能据此当作公共网关的替代鉴权。

因此，文档解释了官方设计，但没有从技术上消除浏览器可见凭据的风险。用户于 2026-10-13 明确批准唯一的本机会话例外：只在 `127.0.0.1`／`localhost` 验证页手动输入，凭据仅存当前页面内存，不持久化、不记录、不提交；生产和公开部署仍禁止使用该方式。

### 3. 正式打断接口已经确认

新版提供：

- 构造参数 `enableClientInterrupt`，默认值为 `true`；
- `agent.interrupt(type)`，`type` 是业务自定义的打断类型；
- 返回值为打断耗时（毫秒）；
- 打断只停止当前数字人播报，不会关闭麦克风或 ASR；
- 如果不再监听，需要另行调用 `agent.stopASR()`；
- `AgentConversationState` 包含 `interrupted`，可用于识别被打断状态。

所以真实打断不再需要用 `interactiveidle()` 猜测，后续应使用 `interrupt()`。

### 4. 播报和完成状态已经确认

- `agent.ask(text)`：经过 Brain 大模型，再由数字人播报；返回只表示消息已写入，不表示播报完成。
- `agent.speak(ssml, is_start?, is_end?, extra?)`：跳过大模型，直接按 SSML 播报；返回本次播报的请求 ID。
- 流式播报可用 `is_start`、`is_end` 标记首段和末段。
- 顶层 `onSpeakStateChange(state, client_speak_id)` 可关联播报请求。
- Agent 回调 `onSpeakStateChange(event)` 的状态包括 `speak_start`、`speak_end`、`speak_error`。
- `onConversationChange(event)` 可得到 `idle`、`asking`、`speaking-directly`、`speaking`、`completed`、`interrupted`、`failed`。

公开文档仍未明确说明在上一段未结束时再次调用完整 `speak()` 是排队、覆盖还是并行，也未给出明确调用频率和文本长度限制。这些需要实测，不能先假定。

### 5. 图片与字幕回调已经确认，但详细结构不足

新版明确提供：

- `onWidgetEvent(data)`：接收字幕、图片等内置 Widget 事件；
- `proxyWidget`：按事件类型自定义分发；
- `IRawWidgetData`：只明确包含 `type: string`，其余字段是开放的未知字段。

当前公开的类型文档没有列出 `widget_pic`、`subtitle_on`、`subtitle_off` 的完整字段，也没有说明打断后旧事件的清理顺序。因此第一版继续由宿主网页自己控制审核图片和大字字幕，使用本地场景状态清除旧内容；不要依赖未验证的 Widget 字段。

### 6. 已确认的其他能力

- `startASR()`、`stopASR()`：麦克风和语音识别；必须由用户手势触发，并运行在 HTTPS 或 localhost。
- `idle()`、`listen()`、`interactiveidle()`：状态表现切换。
- `changeLayout()`：动态调整位置、缩放和朝向，但不是口型幅度控制。
- `setVolume(0–1)`：设置音量。
- `changeAvatarVisible()`、`switchInvisibleMode()`：控制显示和隐身。
- 自动断线重连；恢复后不会自动重放之前的文本、音频或操作。
- 官方仍未提供口型幅度、viseme 或 BlendShape 强度参数。

## 当前阻塞与下一决策

| 项目 | 状态 | 说明 |
|---|---|---|
| 普通话平台预览 | 已通过 | 短句、时间数字、长句可正常播报 |
| 客户端打断接口 | 文档已确认 | `agent.interrupt(type)`，待真实 SDK 实测 |
| 播报完成回调 | 文档已确认 | 可用播报状态和对话状态，待真实 SDK 实测 |
| 图片与字幕宿主联动 | 已通过 | 当前验证页由本项目自己控制 |
| Widget 详细数据 | 部分确认 | 有回调，字段和清理顺序仍不完整 |
| 口型幅度调节 | 未支持 | 保留当前配置，以字幕和图片辅助 |
| 公共网关短期 Token | 未找到 | 官方采用浏览器侧 `appSecret` |
| 与本项目密钥规则的冲突 | 本机例外已批准 | 只允许每次手动输入、仅存页面内存；生产环境仍未解决 |

## 安全边界

- 不在本文、聊天、前端源代码、`.env` 或 Git 仓库中保存 App ID、App Secret。
- 本机例外只允许用户在 `127.0.0.1`／`localhost` 页面逐次手动输入，凭据仅存在当前页面和 SDK 内存中。
- 不写入日志、截图、Cookie、localStorage、sessionStorage 或数据库；页面关闭或手动断开时调用 `destroy()`。
- 该例外不适用于 ASR、Brain、大模型或其他高权限服务端密钥。
- 本地验证方式禁止用于公开部署；生产环境仍需要重新评估凭据和访问控制。

数字人若以“我是豆包”等通用人设回答，需在魔珐控制台的 Brain／角色人设／知识库中配置“知应”身份与边界，不能由本项目网页代码训练。人设原文与配置位置见 [`知应数字人人设.md`](知应数字人人设.md)。

## 端到端语音链路与错误码补充（2026-10-16）

- 语音交流完全使用魔珐自身端到端能力：`startASR()` 持续监听 → 魔珐 ASR → 魔珐 Brain → 数字人语音与口型；豆包只用于工作人员端 Seedream 生图，不进入问答或播报链路。
- `agentCallbacks` 增加 `onASRStateChange`、`onASRResult`、`onLLMResponse`；识别文本与回答只存当前页面内存，不写知应后端、浏览器存储或日志。
- 官方构造参数增加 `audio.inputEnabled=false`、`features`（语义抗干扰、VAD 合并、音量／重复文本检测）与 `reconnect`；麦克风只在老人明确点击后由 `startASR()` 打开，连接恢复后不自动重新开麦。
- 数字人姿态复用 `listen()`／`idle()`，音量复用 `setVolume()`；本轮未启用记忆、知识库、Workflow 或业务工具调用，这些需魔珐控制台与真实业务授权。
- 官方 SDK 源码将错误码 `10007` 定义为 `SOCKET_DISCONNECT`（对外映射 3107），属于可自动恢复的语音 socket 断线，不是老人业务错误；本项目已按可恢复断线处理，不写入“您想怎么做？”反馈区。
- 只有收到真实开口回调（`onVoiceStateChange` start 或 `onSpeakStateChange` `speak_start`）才认定数字人已发声；Brain 文字完成但短时未开口时，用同一魔珐实例补播并只允许一次受控重试。
