# 记忆文件（模板）

Hermes 的 profile 会在这个目录放三份纯文本记忆，每轮对话都注入系统提示词。**不要提交自己的版本**（`.gitignore` 已排除）。

| 文件 | 装什么 | 上限 |
|---|---|---|
| `MEMORY.md` | 你这台机器的环境事实、踩过的坑、项目约定 | 2200 字符 |
| `USER.md` | 用户偏好：关心什么级别的票、说话风格、禁忌 | 1375 字符 |
| `owner.md` | ClawChat 侧的身份与长期项目背景（昵称/bio/behavior 也缓存在这里） | — |

## 这个 bot 值得写进 MEMORY.md 的东西

```markdown
§
标的池 = 通达信自选股 zxg.blk，客户端加票即生效；指数（999999 等）只作大盘背景，不出买卖点。
§
状态纪律：候选 ≠ 已确认。判断不了写「信息不足」，不为凑结论而编。
§
推送通道默认 ClawChat（config.json push.channel=clawchat）；飞书通道 .env FEISHU_ENABLED=0 已停用。
§
本机 pytdx 在线行情在部分网络会失效；排查顺序：tdx/watch.py scan → 换主站 → 看 K 线根数是否足够。
```

## 关于 Honcho（可选）

`config/hermes-profile.example.yaml` 里 `memory.provider: honcho` 表示改用 Honcho 做共享语义记忆（多个 bot 之间共享同一份对用户的认知）。不想自托管 Postgres + Redis 就把这行留空，走原生 `MEMORY.md` + `session_search`。